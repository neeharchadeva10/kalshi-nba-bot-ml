"""H4 step 2: long-running read-only forward collector for Kalshi's Liquidity Incentive Program (`kpm lip-collect`).

Public GET endpoints only, single-threaded, <= ~5.9 requests/second. State lives in data/lip_forward.sqlite so it can be
restarted at any time. Raw responses for tracked markets go to hourly gzip JSONL files (data/raw/lip_forward/YYYYMMDD/HH.jsonl.gz);
CachedClient is called with persist=False because its hash-keyed cache would create one new file per (min_ts, cursor)
request forever. See PREREGISTRATION.md, "H4 step 2 amendment".
"""
from __future__ import annotations

import gzip
import json
import logging
import logging.handlers
import os
import re
import sqlite3
import time
import traceback
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from kpm.analysis.lip_forward import estimate_detail
from kpm.clients.kalshi import KalshiClient
from kpm.core import config
from kpm.core.timeutil import parse_iso

log = logging.getLogger("kpm.lip_collect")

REFRESH_S = 900
BOOK_EVERY_S = 60
HOURLY_S = 3600
RESELECT_S = 120
TOP_N = 25
MIN_EST_USD = 1.0
QS = (10, 50)
MIN_INTERVAL_S = 0.18
STALE_SCAN_S = 7200
SCAN_ROW_EVERY_S = 900          # a tracked market's book is re-scored every minute, but a scans row is written this often
MAX_TRADE_PAGES = 40
SETTLED_STATUS = {"settled", "finalized"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS programs (id TEXT PRIMARY KEY, market_ticker TEXT NOT NULL, start_ts INTEGER, end_ts INTEGER,
  period_reward_usd REAL, target REAL, discount REAL, max_reward_per_account REAL, first_seen INTEGER, last_seen INTEGER);
CREATE INDEX IF NOT EXISTS ix_programs_market ON programs(market_ticker);
CREATE TABLE IF NOT EXISTS scans (ts INTEGER, market TEXT, program_id TEXT, yes_best REAL, no_best REAL, yes_depth REAL,
  no_depth REAL, est_reward_q10 REAL, est_reward_q50 REAL, excluded_now INTEGER);
CREATE INDEX IF NOT EXISTS ix_scans_prog ON scans(program_id, ts);
CREATE TABLE IF NOT EXISTS latest (program_id TEXT PRIMARY KEY, market TEXT, ts INTEGER, q10 REAL, q50 REAL, excluded INTEGER);
CREATE TABLE IF NOT EXISTS tracked (market TEXT, program_id TEXT, q INTEGER, start_ts INTEGER, end_ts INTEGER, reason_end TEXT,
  ended_ts INTEGER, PRIMARY KEY (program_id, q, start_ts));
CREATE TABLE IF NOT EXISTS books (ts INTEGER, market TEXT, yes_levels TEXT, no_levels TEXT, PRIMARY KEY (market, ts)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS trades (trade_id TEXT PRIMARY KEY, ticker TEXT, ts REAL, yes_price REAL, count REAL, taker_side TEXT);
CREATE INDEX IF NOT EXISTS ix_trades_tk ON trades(ticker, ts);
CREATE TABLE IF NOT EXISTS series (series_ticker TEXT PRIMARY KEY, fee_type TEXT, fee_multiplier REAL, raw_json TEXT);
CREATE TABLE IF NOT EXISTS markets (ticker TEXT PRIMARY KEY, event_ticker TEXT, series_ticker TEXT, fee_type TEXT, tick REAL,
  status TEXT, result TEXT, close_ts INTEGER, settled_ts INTEGER, settled INTEGER DEFAULT 0, updated_ts INTEGER, raw_json TEXT);
"""


def _frac_ts(s: str | None) -> float | None:
    t = parse_iso(s)
    if t is None:
        return None
    m = re.search(r"\.(\d+)", s)
    return t + (float("0." + m.group(1)) if m else 0.0)


def _levels(ob: dict, side: str) -> list[tuple[float, float]]:
    lv = [(float(p), float(q)) for p, q in (ob.get(f"{side}_dollars") or [])]
    return sorted((x for x in lv if x[1] > 0), key=lambda x: -x[0])


def _dump_levels(lv: list[tuple[float, float]]) -> str:
    return json.dumps([[p, q] for p, q in lv], separators=(",", ":"))


def _rss_mb() -> int | None:
    """Working set / RSS of this process in MB (no psutil dependency)."""
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
            k32, ps = ctypes.windll.kernel32, ctypes.windll.psapi
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            ps.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
            pmc = PMC()
            pmc.cb = ctypes.sizeof(PMC)
            ps.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
            return int(pmc.WorkingSetSize // 1_000_000)
        import resource
        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024)
    except Exception:
        return None


class RawLog:
    """Hourly gzip JSONL. Each record is a self-contained gzip member (sync-flushed), so a crash loses nothing."""

    def __init__(self, root: Path):
        self.root, self.cur, self.fh = root, None, None

    def write(self, kind: str, path: str, params: dict | None, data) -> None:
        now = datetime.now(timezone.utc)
        name = (now.strftime("%Y%m%d"), now.strftime("%H"))
        if name != self.cur:
            self.close()
            d = self.root / name[0]
            d.mkdir(parents=True, exist_ok=True)
            self.fh = gzip.open(d / f"{name[1]}.jsonl.gz", "ab", compresslevel=6)
            self.cur = name
        rec = {"t": round(time.time(), 3), "kind": kind, "path": path, "params": params, "resp": data}
        self.fh.write((json.dumps(rec, separators=(",", ":")) + "\n").encode())
        self.fh.flush()

    def close(self) -> None:
        if self.fh:
            try:
                self.fh.close()
            except Exception:
                pass
        self.fh, self.cur = None, None


class Collector:
    def __init__(self, max_minutes: float | None = None, db_path: Path | None = None):
        root = config.PROJECT_ROOT
        self.db_path = db_path or root / "data" / "lip_forward.sqlite"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.logs_dir = config.path("logs_dir")
        self.heartbeat_path = self.logs_dir / "lip_collect.heartbeat"
        self.raw = RawLog(root / "data" / "raw" / "lip_forward")
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self.client = KalshiClient()
        self.http = self.client.http
        self.http.min_interval_s = MIN_INTERVAL_S
        self.http.max_retries = 3
        self.http.timeout_s = 20.0
        self.deadline = time.time() + max_minutes * 60 if max_minutes else None
        self.started = time.time()
        self.tracked: dict[tuple[str, int], dict] = {}     # (program_id, q) -> {market, end_ts}
        self.mstate: dict[str, dict] = {}                  # market -> {last_fetch, last_trade_ts, last_scan_row}
        self.queue: deque[str] = deque()
        self.consec_err = 0
        self.recent: deque[float] = deque()
        self.c = dict(requests=0, errors=0, programs=0, scans=0, books=0, trades=0, cycles=0, tracked_open=0,
                      peak_req_1s=0, queue=0, refreshes=0, last_error="")
        self.next_refresh = 0.0
        self.next_hourly = time.time() + 600
        self.next_reselect = 0.0
        self.last_hb = 0.0
        self.last_raw_programs_hour = None
        self._load_state()

    # ---------------------------------------------------------------- http
    def get(self, path: str, params: dict | None = None):
        try:
            data = self.http.get(path, params, use_cache=False, persist=False)
        except Exception as exc:
            self.c["errors"] += 1
            self.consec_err += 1
            self.c["last_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            log.warning("GET %s failed (%s consecutive): %s", path, self.consec_err, self.c["last_error"])
            if self.consec_err >= 3:
                wait = min(300, 5 * 2 ** (self.consec_err - 3))
                log.warning("backing off %ss", wait)
                self.heartbeat(force=True, status=f"backoff {wait}s")
                time.sleep(wait)
            raise
        self.consec_err = 0
        t = time.monotonic()
        self.recent.append(t)
        while t - self.recent[0] > 1.0:
            self.recent.popleft()
        self.c["peak_req_1s"] = max(self.c["peak_req_1s"], len(self.recent))
        self.c["requests"] += 1
        return data

    # ---------------------------------------------------------------- state
    def _load_state(self) -> None:
        for market, pid, q, end_ts in self.conn.execute(
                "SELECT market, program_id, q, end_ts FROM tracked WHERE reason_end IS NULL"):
            self.tracked[(pid, q)] = {"market": market, "end_ts": end_ts}
        for m in {v["market"] for v in self.tracked.values()}:
            self._init_mstate(m)
        self.c["tracked_open"] = len(self.tracked)
        if self.tracked:
            log.info("resumed %d tracked (program,q) rows on %d markets", len(self.tracked), len(self.mstate))

    def _init_mstate(self, m: str) -> None:
        if m in self.mstate:
            return
        r = self.conn.execute("SELECT MAX(ts) FROM trades WHERE ticker=?", (m,)).fetchone()[0]
        s = self.conn.execute("SELECT MIN(start_ts) FROM tracked WHERE market=? AND reason_end IS NULL", (m,)).fetchone()[0]
        self.mstate[m] = {"last_fetch": 0.0, "last_trade_ts": int(r) if r else (s or int(time.time())),
                          "last_scan_row": 0}

    def heartbeat(self, force: bool = False, status: str = "running") -> None:
        now = time.time()
        if not force and now - self.last_hb < 10:
            return
        self.last_hb = now
        self.c["queue"] = len(self.queue)
        self.c["tracked_open"] = len(self.tracked)
        try:
            rss = _rss_mb()
            self.c["peak_rss_mb"] = max(self.c.get("peak_rss_mb") or 0, rss or 0)
            hb = {"status": status, "ts": int(now), "iso": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "uptime_s": int(now - self.started), "rss_mb": rss, "tracked_markets": len(self.mstate), **self.c,
                  "http_retries": self.http.stats["retries"]}
            tmp = self.heartbeat_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(hb), encoding="utf-8")
            os.replace(tmp, self.heartbeat_path)
        except Exception as exc:
            log.warning("heartbeat failed: %s", exc)

    # ---------------------------------------------------------------- programs
    def refresh_programs(self, now: float) -> None:
        rows, cursor = [], None
        while True:
            page = self.get("/incentive_programs", {"status": "active", "type": "liquidity", "limit": 10000,
                                                    "cursor": cursor})
            rows += page.get("incentive_programs", [])
            cursor = page.get("next_cursor")
            if not cursor:
                break
        hour = int(now // 3600)
        if hour != self.last_raw_programs_hour:       # raw program list once per hour is plenty (it is static metadata)
            self.raw.write("programs", "/incentive_programs", {"status": "active", "type": "liquidity"}, rows)
            self.last_raw_programs_hour = hour
        n = int(now)
        data = []
        for p in rows:
            try:
                mx = p.get("max_reward_per_account")
                data.append((p["id"], p["market_ticker"], parse_iso(p["start_date"]), parse_iso(p["end_date"]),
                             float(p["period_reward"]) / 10_000, float(p["target_size_fp"]),
                             float(p["discount_factor_bps"]) / 10_000, float(mx) / 10_000 if mx is not None else None,
                             n, n))
            except Exception as exc:
                log.warning("bad program row %s: %s", p.get("id"), exc)
        self.conn.executemany(
            "INSERT INTO programs VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET market_ticker=excluded.market_ticker,"
            "start_ts=excluded.start_ts,end_ts=excluded.end_ts,period_reward_usd=excluded.period_reward_usd,"
            "target=excluded.target,discount=excluded.discount,max_reward_per_account=excluded.max_reward_per_account,"
            "last_seen=excluded.last_seen", data)
        self.conn.commit()
        self.c["programs"] = len(data)
        self.c["refreshes"] += 1
        self.build_queue(now)
        log.info("refresh: %d active programs, queue=%d, tracked=%d", len(data), len(self.queue), len(self.tracked))

    def build_queue(self, now: float) -> None:
        tracked_markets = {v["market"] for v in self.tracked.values()}
        rows = self.conn.execute(
            "SELECT p.market_ticker, MAX(p.period_reward_usd), MAX(l.ts) FROM programs p LEFT JOIN latest l ON l.program_id=p.id "
            "WHERE p.last_seen>=? AND p.end_ts>? GROUP BY p.market_ticker", (int(now) - 1800, int(now))).fetchall()
        rows = [r for r in rows if r[0] not in tracked_markets]
        # new programs (never scanned) first, then highest reward; rescans of already-scanned markets afterwards
        rows.sort(key=lambda r: (r[2] is not None, -r[1]))
        self.queue = deque(r[0] for r in rows)

    # ---------------------------------------------------------------- scoring
    def _tick(self, market: str) -> float:
        r = self.conn.execute("SELECT tick FROM markets WHERE ticker=?", (market,)).fetchone()
        return r[0] if r and r[0] else 0.01

    def score_book(self, market: str, ob: dict, now: float, write_scan: bool = True) -> None:
        yes, no = _levels(ob, "yes"), _levels(ob, "no")
        tick = self._tick(market)
        progs = self.conn.execute(
            "SELECT id, period_reward_usd, target, discount, start_ts, end_ts FROM programs WHERE market_ticker=? AND end_ts>?",
            (market, int(now))).fetchall()
        n = int(now)
        for pid, rew, target, disc, st, en in progs:
            prog = {"period_reward_usd": rew, "target": target, "discount": disc, "start_ts": st, "end_ts": en}
            e10 = estimate_detail(prog, yes, no, 10, now, tick)
            e50 = estimate_detail(prog, yes, no, 50, now, tick)
            self.conn.execute("INSERT OR REPLACE INTO latest VALUES (?,?,?,?,?,?)",
                              (pid, market, n, e10[0], e50[0], int(e10[3])))
            if write_scan:
                self.conn.execute("INSERT INTO scans VALUES (?,?,?,?,?,?,?,?,?,?)",
                                  (n, market, pid, yes[0][0] if yes else None, no[0][0] if no else None,
                                   sum(q for _, q in yes), sum(q for _, q in no), e10[0], e50[0], int(e10[3])))

    def scan_one(self) -> None:
        market = self.queue.popleft()
        try:
            ob = self.get(f"/markets/{market}/orderbook").get("orderbook_fp") or {}
        except Exception:
            return
        self.score_book(market, ob, time.time())
        self.c["scans"] += 1
        if self.c["scans"] % 25 == 0:
            self.conn.commit()
        if not self.queue:
            self.conn.commit()
            self.next_reselect = 0            # scan pass finished -> reselect now

    # ---------------------------------------------------------------- selection
    def reselect(self, now: float) -> None:
        n = int(now)
        rows = self.conn.execute(
            "SELECT l.program_id, p.market_ticker, l.q10, l.q50, p.start_ts, p.end_ts FROM latest l JOIN programs p ON p.id=l.program_id "
            "WHERE p.end_ts>? AND p.start_ts<=? AND l.ts>=? AND p.last_seen>=?",
            (n, n, n - STALE_SCAN_S, n - 2 * REFRESH_S)).fetchall()
        want: dict[tuple[str, int], tuple[str, int]] = {}
        for q, idx in ((10, 2), (50, 3)):
            top = sorted((r for r in rows if r[idx] >= MIN_EST_USD), key=lambda r: -r[idx])[:TOP_N]
            for r in top:
                want[(r[0], q)] = (r[1], r[5])
        for key in list(self.tracked):
            info = self.tracked[key]
            if key in want and info["end_ts"] > n:
                continue
            reason = "program_ended" if info["end_ts"] <= n else "fell_out"
            self.conn.execute("UPDATE tracked SET reason_end=?, ended_ts=? WHERE program_id=? AND q=? AND reason_end IS NULL",
                              (reason, n, key[0], key[1]))
            del self.tracked[key]
        for key, (market, end_ts) in want.items():
            if key in self.tracked:
                continue
            self.conn.execute("INSERT OR IGNORE INTO tracked (market, program_id, q, start_ts, end_ts) VALUES (?,?,?,?,?)",
                              (market, key[0], key[1], n, end_ts))
            self.tracked[key] = {"market": market, "end_ts": end_ts}
            self._init_mstate(market)
            log.info("tracking %s program %s q=%d", market, key[0][:8], key[1])
        live = {v["market"] for v in self.tracked.values()}
        for m in list(self.mstate):
            if m not in live:
                del self.mstate[m]
        self.conn.commit()
        self.next_reselect = time.time() + RESELECT_S
        self.ensure_meta()

    # ---------------------------------------------------------------- tracked markets
    def work_market(self, market: str) -> None:
        st = self.mstate[market]
        now = time.time()
        st["last_fetch"] = now
        try:
            resp = self.get(f"/markets/{market}/orderbook")
        except Exception:
            return
        ob = resp.get("orderbook_fp") or {}
        self.raw.write("orderbook", f"/markets/{market}/orderbook", None, resp)
        yes, no = _levels(ob, "yes"), _levels(ob, "no")
        self.conn.execute("INSERT OR REPLACE INTO books VALUES (?,?,?,?)",
                          (int(now), market, _dump_levels(yes), _dump_levels(no)))
        self.c["books"] += 1
        write_scan = now - st["last_scan_row"] >= SCAN_ROW_EVERY_S
        self.score_book(market, ob, now, write_scan=write_scan)
        if write_scan:
            st["last_scan_row"] = now
        self.conn.commit()
        self.fetch_trades(market, st)

    def fetch_trades(self, market: str, st: dict) -> None:
        cursor, rows, newest = None, [], st["last_trade_ts"]
        for _ in range(MAX_TRADE_PAGES):
            params = {"ticker": market, "min_ts": int(st["last_trade_ts"]), "limit": 1000, "cursor": cursor}
            try:
                page = self.get("/markets/trades", params)
            except Exception:
                return                                  # keep last_trade_ts; retried next minute (ids dedupe)
            self.raw.write("trades", "/markets/trades", {k: v for k, v in params.items() if v is not None}, page)
            for t in page.get("trades", []):
                ts = _frac_ts(t.get("created_time"))
                if ts is None:
                    continue
                rows.append((t["trade_id"], t.get("ticker", market), ts, float(t["yes_price_dollars"]),
                             float(t.get("count_fp") or t.get("count") or 0), t.get("taker_side")))
                newest = max(newest, int(ts))
            cursor = page.get("cursor")
            if not cursor:
                break
        else:
            log.warning("trade page cap hit for %s", market)
        if rows:
            before = self.conn.total_changes
            self.conn.executemany("INSERT OR IGNORE INTO trades VALUES (?,?,?,?,?,?)", rows)
            self.c["trades"] += self.conn.total_changes - before
        self.conn.commit()
        st["last_trade_ts"] = newest

    # ---------------------------------------------------------------- market metadata
    def _upsert_market(self, m: dict, now: int) -> None:
        steps = [float(r["step"]) for r in m.get("price_ranges") or [] if r.get("step")]
        tick = min(steps) if steps else 0.01
        status, result = m.get("status") or "", m.get("result") or ""
        settled = int(status in SETTLED_STATUS or result in ("yes", "no"))
        slim = {k: v for k, v in m.items() if not k.startswith("rules_")}
        self.conn.execute(
            "INSERT INTO markets (ticker, event_ticker, tick, status, result, close_ts, settled_ts, settled, updated_ts, raw_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(ticker) DO UPDATE SET event_ticker=excluded.event_ticker,tick=excluded.tick,"
            "status=excluded.status,result=excluded.result,close_ts=excluded.close_ts,settled_ts=excluded.settled_ts,"
            "settled=excluded.settled,updated_ts=excluded.updated_ts,raw_json=excluded.raw_json",
            (m["ticker"], m.get("event_ticker"), tick, status, result, parse_iso(m.get("close_time")),
             parse_iso(m.get("settlement_ts") or m.get("settled_time")), settled, now, json.dumps(slim, separators=(",", ":"))))

    def _fetch_markets(self, tickers: list[str]) -> None:
        for i in range(0, len(tickers), 50):
            batch = tickers[i:i + 50]
            try:
                resp = self.get("/markets", {"tickers": ",".join(batch), "limit": 100})
            except Exception:
                continue
            self.raw.write("markets", "/markets", {"tickers": ",".join(batch)}, resp)
            for m in resp.get("markets", []):
                try:
                    self._upsert_market(m, int(time.time()))
                except Exception as exc:
                    log.warning("market upsert %s: %s", m.get("ticker"), exc)
            self.conn.commit()

    def ensure_meta(self) -> None:
        """Market row (tick, status) for every tracked market, then event->series->fee_type once each."""
        missing = [m for m in self.mstate if not self.conn.execute("SELECT 1 FROM markets WHERE ticker=?", (m,)).fetchone()]
        if missing:
            self._fetch_markets(missing)
        self.resolve_series()

    def resolve_series(self) -> None:
        rows = self.conn.execute("SELECT ticker, event_ticker FROM markets WHERE series_ticker IS NULL AND event_ticker IS NOT NULL "
                                 "AND ticker IN (SELECT market FROM tracked)").fetchall()
        for ticker, ev in rows:
            r = self.conn.execute("SELECT series_ticker FROM markets WHERE event_ticker=? AND series_ticker IS NOT NULL", (ev,)).fetchone()
            sticker = r[0] if r else None
            try:
                if not sticker:
                    resp = self.get(f"/events/{ev}")
                    self.raw.write("event", f"/events/{ev}", None, resp)
                    sticker = (resp.get("event") or {}).get("series_ticker")
                if not sticker:
                    continue
                if not self.conn.execute("SELECT 1 FROM series WHERE series_ticker=?", (sticker,)).fetchone():
                    resp = self.get(f"/series/{sticker}")
                    self.raw.write("series", f"/series/{sticker}", None, resp)
                    s = resp.get("series") or {}
                    self.conn.execute("INSERT OR REPLACE INTO series VALUES (?,?,?,?)",
                                      (sticker, s.get("fee_type"), s.get("fee_multiplier"), json.dumps(s, separators=(",", ":"))))
                self.conn.execute("UPDATE markets SET series_ticker=?, fee_type=(SELECT fee_type FROM series WHERE series_ticker=?) "
                                  "WHERE ticker=?", (sticker, sticker, ticker))
                self.conn.commit()
            except Exception:
                continue

    def hourly(self) -> None:
        tickers = [r[0] for r in self.conn.execute(
            "SELECT DISTINCT t.market FROM tracked t LEFT JOIN markets m ON m.ticker=t.market WHERE m.settled IS NOT 1")]
        # recently-ended 15-minute markets settle quickly, so a long list is only a transient
        if tickers:
            self._fetch_markets(tickers)
        self.resolve_series()
        log.info("hourly: refreshed %d unsettled ever-tracked markets", len(tickers))

    # ---------------------------------------------------------------- main loop
    def run(self) -> None:
        log.info("lip-collect starting (max_minutes=%s)", None if not self.deadline else round((self.deadline - time.time()) / 60, 1))
        self.heartbeat(force=True, status="starting")
        try:
            while self.deadline is None or time.time() < self.deadline:
                try:
                    self.step()
                except KeyboardInterrupt:
                    raise
                except Exception as exc:
                    self.c["errors"] += 1
                    self.c["last_error"] = f"cycle {type(exc).__name__}: {str(exc)[:200]}"
                    log.error("cycle error: %s\n%s", exc, traceback.format_exc())
                    try:
                        self.conn.rollback()
                    except Exception:
                        pass
                    time.sleep(5)
                self.heartbeat()
        except KeyboardInterrupt:
            log.info("interrupted")
        finally:
            try:
                self.conn.commit()
            except Exception:
                pass
            self.heartbeat(force=True, status="stopped")
            self.raw.close()
            self.conn.close()
            log.info("lip-collect stopped: %s", self.c)

    def step(self) -> None:
        now = time.time()
        if now >= self.next_refresh:
            self.next_refresh = now + REFRESH_S
            self.c["cycles"] += 1
            try:
                self.refresh_programs(now)
            except Exception:
                self.next_refresh = now + 60       # retry soon; old queue/tracked state keeps working
            self.next_reselect = 0
        if now >= self.next_hourly:
            self.next_hourly = now + HOURLY_S
            self.hourly()
        if now >= self.next_reselect:
            self.reselect(now)
        due = [m for m, s in self.mstate.items() if now - s["last_fetch"] >= BOOK_EVERY_S]
        if due:
            self.work_market(min(due, key=lambda m: self.mstate[m]["last_fetch"]))
        elif self.queue:
            self.scan_one()
        else:
            time.sleep(0.5)


def setup_logging() -> None:
    logs = config.path("logs_dir")
    h = logging.handlers.RotatingFileHandler(logs / "lip_collect.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger("kpm")
    root.addHandler(h)
    root.setLevel(logging.INFO)


def run(max_minutes: float | None = None) -> None:
    setup_logging()
    Collector(max_minutes=max_minutes).run()
