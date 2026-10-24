"""READ-ONLY real-time lock watcher, GAME-LEVEL markets of 26OCT20BOSDET (KXNBAGAME, KXNBASPREAD, KXNBAWINMARGIN).
Public WebSocket market data only (orderbook_delta). No order / portfolio / cancel code. Commands sent: subscribe, unsubscribe.
Run from repo root: KALSHI_ENV=prod .venv/Scripts/python.exe reports/output/nba_picks/26OCT20/lockwatch_ml.py [--until ISO]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lockwatch as base  # noqa: E402  (read-only helpers: fee, iso, chunks, Watcher)

ROOT = base.ROOT
LOGS = ROOT / "logs"
NAME = "lockwatch_ml_bosdet"
CAP = 10
HB_S = 30.0
IDLE_S = 1.0
SERIES = ("KXNBAGAME", "KXNBASPREAD", "KXNBAWINMARGIN")
TEAMS = {"BOS": "Boston", "DET": "Detroit"}
fee, iso, chunks = base.fee, base.iso, base.chunks


# ------------------------------------------------------------------ market specs
def parse_markets(doc: dict) -> dict:
    """ticker -> spec. spec = dict(kind='W'|'S'|'M', team, k (W/S threshold), lo, hi (M bucket, hi None for 21+), title)."""
    out = {}
    for t, b in doc["books"].items():
        series = t.split("-")[0]
        if series not in SERIES or "26OCT20BOSDET" not in t:
            continue
        title = b.get("title") or ""
        suf = t.split("-")[-1]
        if series == "KXNBAGAME":
            team = suf
            assert team in TEAMS and title == TEAMS[team], (t, title)
            out[t] = dict(kind="W", team=team, k=0.5, title=f"{title} wins")
        elif series == "KXNBASPREAD":
            m = re.fullmatch(r"(BOS|DET)(\d+)", suf)
            team, n = m.group(1), int(m.group(2))
            k = b["floor"]
            assert b.get("strike_type") == "greater" and k == n - 0.5, (t, k)
            mt = re.fullmatch(rf"{TEAMS[team]} wins by over ([\d.]+) points", title)
            assert mt and float(mt.group(1)) == k, (t, title)
            out[t] = dict(kind="S", team=team, k=k, title=title)
        else:
            m = re.fullmatch(r"(BOS|DET)(\d+)(?:TO(\d+)|PLUS)", suf)
            team, lo = m.group(1), int(m.group(2))
            hi = int(m.group(3)) if m.group(3) else None
            mt = re.fullmatch(rf"{TEAMS[team]} wins by (\d+)(?:-(\d+)|\+) points", title)
            assert mt and int(mt.group(1)) == lo and (int(mt.group(2)) if mt.group(2) else None) == hi, (t, title)
            out[t] = dict(kind="M", team=team, lo=lo, hi=hi, title=title)
    return out


def settle(spec: dict, bos_margin: int) -> int:
    """1 if YES settles true. DET margin = -bos_margin; no ties."""
    m = bos_margin if spec["team"] == "BOS" else -bos_margin
    if spec["kind"] in ("W", "S"):
        return int(m > spec["k"])
    return int(m >= spec["lo"] and (spec["hi"] is None or m <= spec["hi"]))


def build_locks(specs: dict) -> list:
    """Every lock structure: dict(kind, legs=[(side,ticker)], payout, key)."""
    locks = []
    ladder = {}
    for team in TEAMS:
        L = sorted((s["k"], t) for t, s in specs.items() if s["team"] == team and s["kind"] in ("W", "S"))
        ladder[team] = L
        for i, (k1, t1) in enumerate(L):
            for k2, t2 in L[i + 1:]:
                locks.append(dict(kind="ladder", legs=[("YES", t1), ("NO", t2)], payout=1))
    for _, x in ladder["BOS"]:
        for _, y in ladder["DET"]:
            locks.append(dict(kind="cross_no", legs=[("NO", x), ("NO", y)], payout=1))
    for team in TEAMS:
        S = {k: t for k, t in ladder[team]}
        for t, s in specs.items():
            if s["team"] != team or s["kind"] != "M":
                continue
            if s["hi"] is None:
                if 20.5 in S:
                    locks.append(dict(kind="tail_a", legs=[("YES", t), ("NO", S[20.5])], payout=1))
                    locks.append(dict(kind="tail_b", legs=[("NO", t), ("YES", S[20.5])], payout=1))
            else:
                lo_k, hi_k = s["lo"] - 0.5, s["hi"] + 0.5
                if lo_k in S and hi_k in S:
                    locks.append(dict(kind="bucket_cheap", legs=[("YES", t), ("YES", S[hi_k]), ("NO", S[lo_k])], payout=1))
                    locks.append(dict(kind="bucket_rich", legs=[("NO", t), ("NO", S[hi_k]), ("YES", S[lo_k])], payout=2))
    bk = sorted(t for t, s in specs.items() if s["kind"] == "M")
    if len(bk) == 8:
        locks.append(dict(kind="me_buckets_yes", legs=[("YES", t) for t in bk], payout=1))
        locks.append(dict(kind="me_buckets_no", legs=[("NO", t) for t in bk], payout=7))
    ws = sorted(t for t, s in specs.items() if s["kind"] == "W")
    if len(ws) == 2:
        locks.append(dict(kind="me_win_yes", legs=[("YES", t) for t in ws], payout=1))
        locks.append(dict(kind="me_win_no", legs=[("NO", t) for t in ws], payout=1))
    for lk in locks:
        lk["key"] = (lk["kind"],) + tuple(lk["legs"])
    return locks


def eval_lock(lk: dict, tops_fn):
    """tops_fn(t) -> (bid,bsz,ask,asz) or None. -> result dict if profit > 0 else None."""
    prices, sizes = [], []
    for side, t in lk["legs"]:
        top = tops_fn(t)
        if not top:
            return None
        bid, bsz, ask, asz = top
        if side == "YES":
            if ask is None:
                return None
            prices.append(round(ask, 4)); sizes.append(asz)
        else:
            if bid is None:
                return None
            prices.append(round(1 - bid, 4)); sizes.append(bsz)
    n = math.floor(min(min(sizes), CAP) + 1e-9)
    if n < 1:
        return None
    profit = n * lk["payout"] - n * sum(prices) - sum(fee(p, n) for p in prices)
    if profit <= 1e-9:
        return None
    return dict(kind=lk["kind"], prices=prices, sizes=sizes, n=n, payout=lk["payout"], profit=round(profit, 4),
                per_contract=round(profit / n, 4))


class Detector:
    def __init__(self, specs: dict, tops_fn, emit):
        self.specs, self.tops_fn, self.emit = specs, tops_fn, emit
        self.locks = build_locks(specs)
        self.by_ticker: dict = {}
        for lk in self.locks:
            for _, t in lk["legs"]:
                self.by_ticker.setdefault(t, []).append(lk)
        self.open: dict = {}
        self.n_opened = 0

    def legstr(self, lk):
        return [f"{s} {t} ({self.specs[t]['title']})" for s, t in lk["legs"]]

    def update(self, t: str, now: float):
        for lk in self.by_ticker.get(t, []):
            r = eval_lock(lk, self.tops_fn)
            key = lk["key"]
            st = self.open.get(key)
            if r and st is None:
                self.open[key] = dict(r, t_open=now, max_profit=r["profit"], max_n=r["n"], legs=self.legstr(lk), first=r)
                self.n_opened += 1
                self.emit("lock_open", self.open[key], now)
            elif r:
                st["max_profit"] = max(st["max_profit"], r["profit"])
                st["max_n"] = max(st["max_n"], r["n"])
            elif st is not None:
                self.emit("lock_close", self.open.pop(key), now)

    def close_all(self, now: float):
        for key in list(self.open):
            self.emit("lock_close", self.open.pop(key), now)


class Watcher(base.Watcher):
    """Reuses base.Watcher connection / signing / BookSet / reconnect logic; overrides detector, logging, heartbeat, stop."""

    def __init__(self, doc, env, until_ts):
        from kpm.analysis.ws_books import BookSet
        self.bs = BookSet()
        self.specs = parse_markets(doc)
        self.tickers = sorted(self.specs)
        self.env, self.until_ts = env, until_ts
        self.det = Detector(self.specs, self.tops, self.emit)
        self.n_msgs = self.reconnects = self.seq_gaps = self.snapshots = self.errors = 0
        self.log_fh = None
        self._cid = 0
        self._pending, self._subs, self._last_seq = {}, {}, {}
        self.last_hb = 0.0
        self.last_err = None

    def emit(self, ev, st, now):
        if ev == "lock_open":
            self.write(dict(event=ev, time=iso(now), kind=st["kind"], legs=st["legs"], prices=st["prices"], sizes=st["sizes"],
                            n=st["n"], payout=st["payout"], profit=st["profit"], per_contract=st["per_contract"]))
            print(f"{iso(now)} OPEN  {st['kind']} n={st['n']} profit=${st['profit']:.4f} ({st['per_contract']:.4f}/ct) "
                  f"{' + '.join(st['legs'])[:120]}", flush=True)
        else:
            dur = now - st["t_open"]
            self.write(dict(event=ev, time=iso(now), t_open=iso(st["t_open"]), duration_s=round(dur, 3), kind=st["kind"],
                            legs=st["legs"], first_prices=st["first"]["prices"], first_n=st["first"]["n"],
                            max_profit=st["max_profit"], max_n=st["max_n"]))
            print(f"{iso(now)} CLOSE {st['kind']} lived {dur:.3f}s max_profit=${st['max_profit']:.4f} "
                  f"{' + '.join(st['legs'])[:90]}", flush=True)

    def heartbeat(self, now):
        doc = dict(event="heartbeat", time=iso(now), messages=self.n_msgs, valid_books=len(self.bs.sid_of),
                   tickers=len(self.tickers), snapshots=self.snapshots, reconnects=self.reconnects, seq_gaps=self.seq_gaps,
                   errors=self.errors, open_locks=len(self.det.open), locks_opened_total=self.det.n_opened,
                   open_lock_legs=[" + ".join(s["legs"]) for s in self.det.open.values()][:10])
        self.write(doc)
        tmp = LOGS / (NAME + ".heartbeat.tmp")
        tmp.write_text(json.dumps(doc), encoding="utf-8")
        tmp.replace(LOGS / (NAME + ".heartbeat"))
        self.last_hb = now

    def stop_now(self):
        return time.time() >= self.until_ts or (LOGS / (NAME + ".STOP")).exists()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--until", default="2026-10-21T00:00:00Z")
    args = ap.parse_args()
    from kpm.execution.env import load_env
    env = load_env(require_keys=True, expected="prod")
    until = datetime.fromisoformat(args.until.replace("Z", "+00:00"))
    until_ts = (until if until.tzinfo else until.replace(tzinfo=timezone.utc)).timestamp()
    doc = json.loads((HERE / "bosdet.json").read_text(encoding="utf-8"))
    LOGS.mkdir(exist_ok=True)
    w = Watcher(doc, env, until_ts)
    w.log_fh = open(LOGS / (NAME + ".jsonl"), "a", encoding="utf-8")
    print(f"lockwatch_ml: {len(w.tickers)} tickers, {len(w.det.locks)} lock structures; until {iso(until_ts)}; "
          f"stop via logs/{NAME}.STOP", flush=True)
    try:
        asyncio.run(w.run())
    except KeyboardInterrupt:
        print("interrupted")
    finally:
        w.log_fh.close()
    print(f"done: tickers={len(w.tickers)} msgs={w.n_msgs} snapshots={w.snapshots} valid={len(w.bs.sid_of)} "
          f"reconnects={w.reconnects} seq_gaps={w.seq_gaps} errors={w.errors} locks_opened={w.det.n_opened}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
