"""WebSocket recorder on a fake socket: subscribe payloads, the send allowlist, reconnect + re-sign, seq gaps, files."""
from __future__ import annotations

import asyncio
import gzip
import json
from datetime import datetime, timezone

import pytest

from kpm.execution import ws_recorder as wr

URL = "wss://example.invalid/trade-api/ws/v2"


def sub_ok(cid, sid, ch="orderbook_delta"):
    return {"id": cid, "type": "subscribed", "msg": {"channel": ch, "sid": sid}}


def delta(sid, seq, t="T1"):
    return {"type": "orderbook_delta", "sid": sid, "seq": seq,
            "msg": {"market_ticker": t, "price_dollars": "0.5000", "delta_fp": "1.00", "side": "yes"}}


class FakeWS:
    def __init__(self, script):
        self.script, self.sent = list(script), []

    async def send(self, text):
        self.sent.append(json.loads(text))

    async def recv(self):
        if not self.script:
            raise ConnectionResetError("closed")
        item = self.script.pop(0)
        if callable(item):
            item(self)
            return await self.recv()
        if isinstance(item, BaseException):
            raise item
        return item if isinstance(item, str) else json.dumps(item)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeConnect:
    """Hands out one FakeWS per connect; ``scripts`` are lists of messages / exceptions / callables."""

    def __init__(self, scripts):
        self.scripts, self.headers, self.sockets = scripts, [], []

    def __call__(self, url, additional_headers=None):
        assert url == URL
        self.headers.append(additional_headers)
        ws = FakeWS(self.scripts.pop(0))
        self.sockets.append(ws)
        return ws


class Clock:
    def __init__(self, t=1_791_226_200.0):
        self.t = t

    def __call__(self):
        return self.t


def make(tmp_path, scripts, tickers=("T1", "T2"), **kw):
    clock = Clock()
    sleeps, signs = [], []

    async def sleep(s):
        sleeps.append(s)

    def sign():
        signs.append(1)
        return {"sig": str(len(signs))}

    connect = FakeConnect(scripts)
    rec = wr.Recorder(url=URL, tickers=list(tickers), writer=wr.HourlyWriter(tmp_path / "raw"), sign=sign,
                      log_dir=tmp_path / "logs", connect=connect, clock=clock, ns_clock=lambda: int(clock.t * 1e9),
                      sleep=sleep, **kw)
    rec.sleeps, rec.signs, rec.fake_connect, rec.clock_obj = sleeps, signs, connect, clock
    return rec


def stop(rec):
    return lambda ws: setattr(rec, "stop", True)


def lines(tmp_path):
    out = []
    for f in sorted((tmp_path / "raw").glob("*.jsonl.gz")):
        with gzip.open(f, "rt") as fh:
            out += [json.loads(x) for x in fh]
    return out


def ctl_events(tmp_path):
    return [x["ctl"] for x in lines(tmp_path) if "ctl" in x]


def run(rec):
    asyncio.run(rec.run())


def test_subscribe_payloads_and_chunking(tmp_path):
    tickers = [f"T{i}" for i in range(450)]
    script = []
    rec = make(tmp_path, [script], tickers=tickers)
    script.append(stop(rec))
    run(rec)
    sent = rec.fake_connect.sockets[0].sent
    assert [(c["cmd"], c["params"]["channels"], len(c["params"]["market_tickers"])) for c in sent] == \
        [("subscribe", ["orderbook_delta"], n) for n in (200, 200, 50)] + [("subscribe", ["trade"], n) for n in (200, 200, 50)]
    assert [c["id"] for c in sent] == [1, 2, 3, 4, 5, 6]
    assert sent[0]["params"]["market_tickers"][0] == "T0"


def test_no_trades_flag(tmp_path):
    script = []
    rec = make(tmp_path, [script], trades=False)
    script.append(stop(rec))
    run(rec)
    assert [c["params"]["channels"] for c in rec.fake_connect.sockets[0].sent] == [["orderbook_delta"]]


@pytest.mark.parametrize("cmd", [{"cmd": "create_order", "params": {}}, {"cmd": "cancel_order"}, {"cmd": "batch_create_orders"},
                                 {"cmd": "subscribe", "params": {"channels": ["fill"]}},
                                 {"cmd": "subscribe", "params": {"channels": ["orderbook_delta", "user_orders"]}}, {}])
def test_send_allowlist_blocks_before_sending(tmp_path, cmd):
    rec = make(tmp_path, [[]])
    ws = FakeWS([])
    with pytest.raises(wr.CommandNotAllowed):
        asyncio.run(rec.send_cmd(ws, cmd))
    assert ws.sent == []


def test_allowlist_constants():
    assert wr.ALLOWED_CMDS == {"subscribe", "unsubscribe", "update_subscription"}
    assert wr.ALLOWED_CHANNELS == {"orderbook_delta", "trade"}


def test_reconnect_resigns_resubscribes_and_backs_off(tmp_path):
    scripts = [[ConnectionResetError("boom")], [ConnectionResetError("boom")], [ConnectionResetError("boom")], []]
    rec = make(tmp_path, scripts)
    scripts[3] += [stop(rec), delta(5, 1)]
    run(rec)
    assert rec.signs == [1, 1, 1, 1]                       # signed afresh on every connect
    assert [h["sig"] for h in rec.fake_connect.headers] == ["1", "2", "3", "4"]
    assert rec.sleeps == [1.0, 2.0, 4.0]
    assert all(len(s.sent) == 2 for s in rec.fake_connect.sockets)        # each connection resubscribed everything
    assert rec.reconnects == 3
    ev = [e["event"] for e in ctl_events(tmp_path)]
    assert ev.count("connect") == 4 and ev.count("disconnect") == 3 and ev[-1] == "stop"


def test_backoff_caps_at_30(tmp_path):
    scripts = [[ConnectionResetError()] for _ in range(8)] + [[]]
    rec = make(tmp_path, scripts)
    scripts[-1].append(stop(rec))
    run(rec)
    assert rec.sleeps == [1, 2, 4, 8, 16, 30, 30, 30]


def test_seq_gap_triggers_resubscribe(tmp_path):
    script = [sub_ok(1, 5), sub_ok(2, 6, "trade"), delta(5, 1), delta(5, 2), delta(5, 4), delta(5, 5), sub_ok(3, 9)]
    rec = make(tmp_path, [script])
    script.append(stop(rec))
    run(rec)
    sent = rec.fake_connect.sockets[0].sent
    assert sent[2] == {"cmd": "unsubscribe", "params": {"sids": [5]}, "id": 3}
    assert sent[3]["cmd"] == "subscribe" and sent[3]["params"] == {"channels": ["orderbook_delta"], "market_tickers": ["T1", "T2"]}
    assert rec.seq_gaps == 1
    gap = [e for e in ctl_events(tmp_path) if e["event"] == "seq_gap"][0]
    assert (gap["sid"], gap["expected"], gap["got"]) == (5, 3, 4)
    assert any(e["event"] == "resubscribe" for e in ctl_events(tmp_path))
    assert rec.counts["orderbook_delta"] == 4 and rec.counts["subscribed"] == 3


def test_multi_chunk_gap_resubscribes_every_chunk(tmp_path):
    ok = lambda cid, seq: {"type": "ok", "id": cid, "sid": 5, "seq": seq, "msg": {"market_tickers": []}}
    script = [sub_ok(1, 5), ok(2, 1), ok(3, 2), delta(5, 4)]
    rec = make(tmp_path, [script], tickers=[f"T{i}" for i in range(450)], trades=False)
    script.append(stop(rec))
    run(rec)
    sent = rec.fake_connect.sockets[0].sent
    assert [c["cmd"] for c in sent] == ["subscribe"] * 3 + ["unsubscribe"] + ["subscribe"] * 3
    again = [t for c in sent[4:] for t in c["params"]["market_tickers"]]
    assert again == [f"T{i}" for i in range(450)]
    assert rec.seq_gaps == 1                          # the ok replies counted as seq numbers: no false gap


def test_trade_gap_is_logged_not_resubscribed(tmp_path):
    def tr(s):
        return {"type": "trade", "sid": 6, "seq": s, "msg": {"market_ticker": "T1"}}
    script = [sub_ok(1, 5), sub_ok(2, 6, "trade"), tr(1), tr(3)]
    rec = make(tmp_path, [script])
    script.append(stop(rec))
    run(rec)
    assert len(rec.fake_connect.sockets[0].sent) == 2 and rec.seq_gaps == 1


def test_raw_messages_written_verbatim_with_error_ctl(tmp_path):
    err = '{"id":1,"type":"error","msg":{"code":2,"msg":"Params required"}}'
    script = ["not json", err]
    rec = make(tmp_path, [script])
    script.append(stop(rec))
    run(rec)
    got = lines(tmp_path)
    assert [x["raw"] for x in got if "raw" in x] == ["not json", err]
    assert all(isinstance(x["recv_ns"], int) for x in got)
    assert rec.counts["unparsed"] == 1 and rec.counts["error"] == 1
    assert any(e["event"] == "error" for e in ctl_events(tmp_path))


def test_error_text_is_scrubbed(tmp_path):
    scripts = [[RuntimeError("bad key SECRETID123 here")], []]
    rec = make(tmp_path, scripts, scrub=("SECRETID123",))
    scripts[1].append(stop(rec))
    run(rec)
    text = json.dumps(lines(tmp_path))
    assert "SECRETID123" not in text and "<redacted>" in text


def test_hour_roll_and_flush(tmp_path):
    rec = make(tmp_path, [[]])
    h1 = datetime(2026, 10, 22, 0, 59, 59, tzinfo=timezone.utc).timestamp()
    rec.writer.write(int(h1 * 1e9), {"raw": "a"})
    rec.writer.write(int((h1 + 2) * 1e9), {"raw": "b"})
    rec.writer.flush()
    names = sorted(p.name for p in (tmp_path / "raw").iterdir())
    assert len(names) == 2 and names[0].startswith("msgs-2026102200-") and names[1].startswith("msgs-2026102201-")
    data = ""
    with gzip.open(tmp_path / "raw" / names[1], "rt") as fh:    # readable before close (crash safety)
        try:
            for line in fh:
                data += line
        except EOFError:                                        # the stream is not finished yet
            pass
    assert '"raw":"b"' in data
    rec.writer.close()


def test_stop_file_and_heartbeat(tmp_path):
    def make_stop_file(ws):
        (tmp_path / "logs").mkdir(exist_ok=True)
        (tmp_path / "logs" / "ws_recorder.STOP").write_text("x")
    script = [sub_ok(1, 5), delta(5, 1), make_stop_file, delta(5, 2)]
    rec = make(tmp_path, [script])
    run(rec)
    hb = json.loads((tmp_path / "logs" / "ws_recorder.heartbeat").read_text())
    assert hb["messages"] == 3 and hb["by_type"]["orderbook_delta"] == 2 and hb["reconnects"] == 0
    assert hb["seq_gaps"] == 0 and hb["last_message_age_s"] == 0.0


def test_until_stops(tmp_path):
    script = []
    rec = make(tmp_path, [script], until_ts=1_791_226_200.0 + 10)
    script += [delta(5, 1), lambda ws: setattr(rec.clock_obj, "t", rec.clock_obj.t + 11), delta(5, 2), delta(5, 3)]
    run(rec)
    assert rec.n_msgs == 2 and rec.reconnects == 0


def test_load_tickers(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"date": "26OCT22", "games": {"A": {"tickers": ["a1", "a2"]}, "B": {"tickers": ["b1", "a1"]}}}))
    assert wr.load_tickers(p) == ("26OCT22", ["a1", "a2", "b1"])
    assert wr.load_tickers(p, ["B"])[1] == ["b1", "a1"]
    with pytest.raises(SystemExit):
        wr.load_tickers(p, ["C"])


def test_parse_until():
    assert wr.parse_until("2026-10-22T03:30:00Z") == wr.parse_until("2026-10-22T03:30:00")
    assert wr.parse_until("2026-10-22T03:30:00Z") == datetime(2026, 10, 22, 3, 30, tzinfo=timezone.utc).timestamp()


def test_tagged_recorder_uses_its_own_stop_and_heartbeat_files(tmp_path):
    def make_stop_files(ws):
        (tmp_path / "logs").mkdir(exist_ok=True)
        (tmp_path / "logs" / "ws_recorder.STOP").write_text("x")           # the old name must not stop a tagged recorder
    rec = make(tmp_path, [[sub_ok(1, 5), delta(5, 1), make_stop_files, delta(5, 2), lambda ws: (
        (tmp_path / "logs" / "ws_recorder-nba-26OCT20.STOP").write_text("x"))]], tag="nba-26OCT20")
    run(rec)
    assert (tmp_path / "logs" / "ws_recorder-nba-26OCT20.heartbeat").exists()
    assert not (tmp_path / "logs" / "ws_recorder.heartbeat").exists()
    assert json.loads((tmp_path / "logs" / "ws_recorder-nba-26OCT20.heartbeat").read_text())["messages"] >= 2


def test_recorder_tag_from_markets_path(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for rel, tag in (("26OCT22", "26OCT22"), ("nba/26OCT20", "nba-26OCT20")):
        d = tmp_path / "data" / "ws" / rel
        d.mkdir(parents=True)
        assert wr.recorder_tag(d / "markets.json") == tag
    assert wr.recorder_tag(tmp_path / "elsewhere" / "markets.json") == "elsewhere"
