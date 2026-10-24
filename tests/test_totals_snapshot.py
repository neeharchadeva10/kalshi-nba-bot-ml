import gzip
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("totals_snapshot", ROOT / "scripts" / "totals_snapshot.py")
T = importlib.util.module_from_spec(spec)
sys.modules["totals_snapshot"] = T
spec.loader.exec_module(T)


class Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code = data, status

    def json(self):
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """Serves /markets by series_ticker; `pages` maps series -> list of pages (cursor chain)."""

    def __init__(self, pages, status=None):
        self.headers = {}
        self.pages, self.status, self.calls = pages, status or {}, []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params), dict(self.headers)))
        s = params["series_ticker"]
        if s in self.status:
            return Resp({}, self.status[s])
        chain = self.pages.get(s, [])
        idx = int(params["cursor"]) if params.get("cursor") else 0
        if idx >= len(chain):
            return Resp({"markets": []})
        page = {"markets": chain[idx]}
        if idx + 1 < len(chain):
            page["cursor"] = str(idx + 1)
        return Resp(page)


def mk(ticker, event="E1", bid="0.4000", ask="0.4500"):
    return {"ticker": ticker, "event_ticker": event, "floor_strike": 44.5, "strike_type": "greater",
            "yes_bid_dollars": bid, "yes_ask_dollars": ask, "yes_bid_size_fp": "10.00", "yes_ask_size_fp": "5.00",
            "last_price_dollars": "0.4200", "volume_fp": "100.00", "open_interest_fp": "50.00", "status": "active",
            "close_time": "2026-10-25T00:00:00Z", "expected_expiration_time": "2026-10-25T03:00:00Z"}


def snap(tmp_path, session, now, series=None, **kw):
    return T.Snapshotter(tmp_path, session=session, now_fn=lambda: now, sleep_fn=lambda s: None,
                         series=series or ["KXNFLTOTAL", "KXNBAGAME"], **kw)


def test_paging_and_rows(tmp_path):
    sess = FakeSession({"KXNFLTOTAL": [[mk("A1"), mk("A2")], [mk("A3")]], "KXNBAGAME": [[mk("B1")]]})
    now = datetime(2026, 10, 24, 1, 5, 9, tzinfo=timezone.utc)
    hb = snap(tmp_path, sess, now).run()
    assert hb["n_markets"] == {"KXNFLTOTAL": 3, "KXNBAGAME": 1} and hb["errors"] == {}
    calls = [c for c in sess.calls if c[1]["series_ticker"] == "KXNFLTOTAL"]
    assert len(calls) == 2 and calls[1][1]["cursor"] == "1"
    assert all(c[1]["status"] == "open" and c[1]["limit"] == 1000 for c in sess.calls)
    rows = T.read_snap(tmp_path / "data" / "paper_tape" / "2026-10-23" / "snap.jsonl.gz")
    assert len(rows) == 4
    r = rows[0]
    assert r["yes_bid"] == "0.4000" and r["yes_bid_f"] == 0.4 and r["yes_ask_size"] == "5.00" and r["ts_utc"] == "2026-10-24T01:05:09Z"
    assert r["series"] == "KXNFLTOTAL" and r["close_time"] and r["expected_expiration_time"]


def test_file_naming_by_et_date(tmp_path):
    sess = FakeSession({"KXNFLTOTAL": [[mk("A1")]]})
    # 2026-10-24 02:30 UTC is still 2026-10-23 22:30 ET (EDT)
    now = datetime(2026, 10, 24, 2, 30, 5, tzinfo=timezone.utc)
    snap(tmp_path, sess, now, series=["KXNFLTOTAL"]).run()
    d = tmp_path / "data" / "paper_tape" / "2026-10-23"
    assert (d / "raw-023005.json.gz").exists() and (d / "snap.jsonl.gz").exists()
    raw = json.loads(gzip.open(d / "raw-023005.json.gz", "rt").read())
    assert raw["series"]["KXNFLTOTAL"][0]["markets"][0]["ticker"] == "A1"
    # after ET midnight -> next folder
    snap(tmp_path, sess, datetime(2026, 10, 24, 4, 1, 0, tzinfo=timezone.utc), series=["KXNFLTOTAL"]).run()
    assert (tmp_path / "data" / "paper_tape" / "2026-10-24" / "raw-040100.json.gz").exists()


def test_append_only_members(tmp_path):
    sess = FakeSession({"KXNFLTOTAL": [[mk("A1")]]})
    t = datetime(2026, 10, 24, 1, 0, 0, tzinfo=timezone.utc)
    snap(tmp_path, sess, t, series=["KXNFLTOTAL"]).run()
    snap(tmp_path, sess, t + timedelta(minutes=5), series=["KXNFLTOTAL"]).run()
    rows = T.read_snap(tmp_path / "data" / "paper_tape" / "2026-10-23" / "snap.jsonl.gz")
    assert [r["ts_utc"] for r in rows] == ["2026-10-24T01:00:00Z", "2026-10-24T01:05:00Z"]


def test_series_skip_404_and_empty(tmp_path):
    sess = FakeSession({"KXNFLTOTAL": [[mk("A1")]], "KXNBAGAME": []}, status={"KXNFLSPREAD": 404})
    hb = snap(tmp_path, sess, datetime(2026, 10, 24, 1, 0, tzinfo=timezone.utc),
              series=["KXNFLSPREAD", "KXNFLTOTAL", "KXNBAGAME"]).run()
    assert hb["n_markets"] == {"KXNFLSPREAD": 0, "KXNFLTOTAL": 1, "KXNBAGAME": 0}
    assert hb["errors"] == {}


def test_series_error_isolated(tmp_path):
    sess = FakeSession({"KXNFLTOTAL": [[mk("A1")]]}, status={"KXNBAGAME": 400})
    hb = snap(tmp_path, sess, datetime(2026, 10, 24, 1, 0, tzinfo=timezone.utc)).run()
    assert hb["n_markets"] == {"KXNFLTOTAL": 1} and "KXNBAGAME" in hb["errors"]


def test_gap_detection(tmp_path):
    sess = FakeSession({"KXNFLTOTAL": [[mk("A1")]]})
    t = datetime(2026, 10, 24, 1, 0, 0, tzinfo=timezone.utc)
    snap(tmp_path, sess, t, series=["KXNFLTOTAL"]).run()                                   # first run: no gap
    snap(tmp_path, sess, t + timedelta(minutes=14), series=["KXNFLTOTAL"]).run()           # 14 min: no gap
    snap(tmp_path, sess, t + timedelta(minutes=14 + 16), series=["KXNFLTOTAL"]).run()      # 16 min: gap
    rows = T.read_snap(tmp_path / "data" / "paper_tape" / "2026-10-23" / "snap.jsonl.gz")
    gaps = [r for r in rows if r["type"] == "gap"]
    assert len(gaps) == 1 and gaps[0]["gap_s"] == 16 * 60 and gaps[0]["ts_utc"] == "2026-10-24T01:30:00Z"


def test_no_auth_header_and_only_markets_endpoint(tmp_path):
    sess = FakeSession({"KXNFLTOTAL": [[mk("A1")]]})
    sess.headers["Authorization"] = "Bearer secret"
    sess.headers["KALSHI-ACCESS-KEY"] = "k"
    snap(tmp_path, sess, datetime(2026, 10, 24, 1, 0, tzinfo=timezone.utc), series=["KXNFLTOTAL"]).run()
    for url, params, headers in sess.calls:
        assert url.endswith("/trade-api/v2/markets")
        assert not any(k.lower() == "authorization" or k.lower().startswith("kalshi-access") for k in headers)
        assert "portfolio" not in url and "orders" not in url


def test_throttle(tmp_path):
    sleeps, clock = [], {"t": 100.0}

    def sleep(s):
        sleeps.append(s)
        clock["t"] += s

    sess = FakeSession({"KXNFLTOTAL": [[mk("A1")]], "KXNBAGAME": [[mk("B1")]]})
    sn = T.Snapshotter(tmp_path, session=sess, now_fn=lambda: datetime(2026, 10, 24, 1, 0, tzinfo=timezone.utc),
                       sleep_fn=sleep, mono_fn=lambda: clock["t"], series=["KXNFLTOTAL", "KXNBAGAME"])
    sn.run()
    assert len(sess.calls) == 2 and sum(sleeps) >= 0.25 - 1e-9     # second request waited out the throttle
    # no real sleeping happened in the simulated clock beyond the throttle waits
    assert all(s <= 0.25 + 1e-9 for s in sleeps)


def test_heartbeat_written(tmp_path):
    sess = FakeSession({"KXNFLTOTAL": [[mk("A1")]]})
    snap(tmp_path, sess, datetime(2026, 10, 24, 1, 0, tzinfo=timezone.utc), series=["KXNFLTOTAL"]).run()
    hb = json.loads((tmp_path / "logs" / "paper_tape.heartbeat").read_text())
    assert hb["n_markets"] == {"KXNFLTOTAL": 1} and hb["errors"] == {} and hb["last_ok_ts_utc"].startswith("2026-10-24T01:00:00")
