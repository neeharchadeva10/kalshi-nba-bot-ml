"""READ-ONLY paired recorder for research log #28 (does Polymarket lead Kalshi in-game? NBA). See PREREGISTRATION.md.

Public market data only:
  * Kalshi: GET REST via the repo's KalshiClient (discovery) + ONE signed WebSocket, orderbook_delta, commands
    subscribe / unsubscribe only (connection, signing and BookSet reused from ../ohio_trial/lockwatch_fast.py).
  * Polymarket: public Gamma REST (GET, User-Agent set) + the public CLOB market WebSocket (no auth, no keys).
No order / portfolio / account code anywhere. Secrets are never printed or logged.

  KALSHI_ENV=prod python reports/output/leadlag/pairrec.py --date 26OCT21 --tag ohio [--until ISO|+Ns] [--out-dir logs]

Outputs in --out-dir (name = pairrec_<date>_<tag>):
  <name>.map.json   discovery + mapping      <name>.jsonl      one JSON object per line      <name>.heartbeat (every 30 s)
  create <name>.STOP to stop.  Default --until = 09:00Z the day after --date.

Record schema (all times: r = time.time_ns() at receipt on ONE clock for both venues; x = exchange ms timestamp or null):
  top change : {"v":"k"|"p","t":ticker|token_id,"r":recv_ns,"x":ts_ms,"b":bid,"bs":bid_size,"a":ask,"as":ask_size}
               (Kalshi prices in dollars of YES; Polymarket prices of that token's outcome = the mapped team's YES)
  book invalid (disconnect / seq gap): {"v":..,"t":..,"r":..,"inv":1}  - the next top record is a fresh snapshot
  events     : {"ev":"start|connect|disconnect|error|stop|seq_gap", "v":"k"|"p"|null, "r":recv_ns, ...}
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent / "ohio_trial"))
import lockwatch_fast as lf  # noqa: E402  (reuse: bookset, loads/dumps, iso, chunks, parse_until, install_uvloop)

GAMMA = "https://gamma-api.polymarket.com"
POLY_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
UA = "Mozilla/5.0 (X11; Linux aarch64) kpm-pairrec/1.0 (read-only research)"
HB_S = 30.0
POLY_PING_S = 10.0
POLY_IDLE_RECONNECT_S = 30.0
FLUSH_S = 1.0
DEAD_STATUS = lf.DEAD_STATUS

NICK2CODE = {
    "hawks": "ATL", "celtics": "BOS", "nets": "BKN", "hornets": "CHA", "bulls": "CHI", "cavaliers": "CLE",
    "mavericks": "DAL", "nuggets": "DEN", "pistons": "DET", "warriors": "GSW", "rockets": "HOU", "pacers": "IND",
    "clippers": "LAC", "lakers": "LAL", "grizzlies": "MEM", "heat": "MIA", "bucks": "MIL", "timberwolves": "MIN",
    "pelicans": "NOP", "knicks": "NYK", "thunder": "OKC", "magic": "ORL", "76ers": "PHI", "suns": "PHX",
    "trail blazers": "POR", "blazers": "POR", "kings": "SAC", "spurs": "SAS", "raptors": "TOR", "jazz": "UTA",
    "wizards": "WAS",
}
CITY2CODE = {
    "atlanta": "ATL", "boston": "BOS", "brooklyn": "BKN", "charlotte": "CHA", "chicago": "CHI", "cleveland": "CLE",
    "dallas": "DAL", "denver": "DEN", "detroit": "DET", "golden state": "GSW", "houston": "HOU", "indiana": "IND",
    "memphis": "MEM", "miami": "MIA", "milwaukee": "MIL", "minnesota": "MIN", "new orleans": "NOP",
    "new york": "NYK", "oklahoma city": "OKC", "orlando": "ORL", "philadelphia": "PHI", "phoenix": "PHX",
    "portland": "POR", "sacramento": "SAC", "san antonio": "SAS", "toronto": "TOR", "utah": "UTA",
    "washington": "WAS",
}
SPREAD_Q = re.compile(r"^Spread:\s*(.+?)\s*\(([+-]?\d+(?:\.\d+)?)\)\s*$")
VS_Q = re.compile(r"^(.+?)\s+vs\.?\s+(.+)$")


# ------------------------------------------------------------------ pure helpers (tested offline)
def split_game(event_ticker: str):
    """'KXNBAGAME-26OCT20PHINYK' -> ('26OCT20PHINYK', '26OCT20', 'PHI', 'NYK') (away, home)."""
    parts = event_ticker.split("-")
    g = parts[1] if len(parts) > 1 else ""
    m = re.fullmatch(r"(\d{2}[A-Z]{3}\d{2})([A-Z]{3})([A-Z]{3})", g)
    return (g, m.group(1), m.group(2), m.group(3)) if m else (g, None, None, None)


def kalshi_code(ticker: str, series: str) -> str | None:
    """Team code from a market ticker: KXNBAGAME-<game>-<CODE>; KXNBASPREAD-<game>-<CODE><N>."""
    parts = ticker.split("-")
    if len(parts) < 3:
        return None
    s = parts[2]
    return s if series == "KXNBAGAME" else (re.match(r"([A-Z]{3})\d+$", s).group(1) if re.match(r"([A-Z]{3})\d+$", s) else None)


def resolve_team(name: str, game_codes, title_map=None) -> str | None:
    """Polymarket team name ('Knicks', '76ers') -> NBA code, constrained to the game's two codes when known."""
    n = (name or "").strip().lower()
    codes = set(game_codes or ())
    cand = None
    for tm in (title_map or {}, NICK2CODE, CITY2CODE):
        if n in tm:
            cand = tm[n]
            break
    if cand is None:
        for k, v in list(NICK2CODE.items()) + list(CITY2CODE.items()):
            if k in n or n in k:
                cand = v
                break
    if cand is None and title_map:
        for k, v in title_map.items():
            if k and (k in n or n in k):
                cand = v
                break
    if cand is None or (codes and cand not in codes):
        return None
    return cand


def build_title_map(markets: list[dict]) -> dict:
    """Kalshi moneyline yes_sub_title ('Philadelphia') / 'Philadelphia wins' -> code."""
    tm = {}
    for m in markets:
        code = m.get("code")
        if not code:
            continue
        for key in (m.get("title"), m.get("sub")):
            if key:
                tm[re.sub(r"\s+wins$", "", key.strip(), flags=re.I).lower()] = code
    return tm


def parse_json_list(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return []
    return v or []


def match_game(kgame: dict, gev: dict) -> tuple[list[dict], list[dict]]:
    """kgame: {game, away, home, ml:{code:{ticker,title,sub}}, spreads:[{ticker,code,floor,title}]}; gev: Gamma event.
    -> (pairs, unmatched). Pairs carry kalshi ticker + polymarket token id (outcome[0] = the named team)."""
    codes = {kgame["away"], kgame["home"]}
    tmap = build_title_map([dict(code=c, title=v.get("title"), sub=v.get("sub")) for c, v in kgame["ml"].items()])
    pairs, unmatched = [], []
    spread_idx = {(s["code"], round(float(s["floor"]), 2)): s for s in kgame["spreads"] if s.get("floor") is not None}
    for m in gev.get("markets") or []:
        q, smt = m.get("question") or "", m.get("sportsMarketType") or ""
        toks, outs = parse_json_list(m.get("clobTokenIds")), parse_json_list(m.get("outcomes"))
        if not toks or not outs:
            continue
        base = dict(game=kgame["game"], poly_question=q, poly_condition=m.get("conditionId"),
                    poly_token=toks[0], poly_token_other=toks[1] if len(toks) > 1 else None, outcomes=outs)
        if smt == "moneyline" or (not smt and VS_Q.match(q) and ":" not in q):
            team = resolve_team(outs[0], codes, tmap)
            other = resolve_team(outs[1], codes, tmap) if len(outs) > 1 else None
            kt = kgame["ml"].get(team, {}).get("ticker") if team else None
            if not kt or (other and other == team):
                unmatched.append(dict(base, why="moneyline team/ticker not resolved", type="moneyline"))
            else:
                pairs.append(dict(base, type="moneyline", team=team, line=None, kalshi=kt))
        elif smt == "spreads":
            mm = SPREAD_Q.match(q)
            if not mm:
                unmatched.append(dict(base, why="spread question unparsed", type="spread"))
                continue
            team, line = resolve_team(mm.group(1), codes, tmap), float(mm.group(2))
            if team is None:
                unmatched.append(dict(base, why="spread team unresolved", type="spread"))
            elif line >= 0:
                unmatched.append(dict(base, why="positive spread line (complement of opponent's Kalshi market)",
                                      type="spread", team=team, line=line))
            elif outs[0] and resolve_team(outs[0], codes, tmap) != team:
                unmatched.append(dict(base, why="outcome[0] is not the named team", type="spread", team=team, line=line))
            else:
                ks = spread_idx.get((team, round(-line, 2)))
                if ks is None:
                    unmatched.append(dict(base, why="no Kalshi spread market at that strike", type="spread",
                                          team=team, line=line))
                else:
                    pairs.append(dict(base, type="spread", team=team, line=line, kalshi=ks["ticker"], floor=ks["floor"]))
    return pairs, unmatched


# ---- Polymarket book maintenance
class PolyBooks:
    """Per-token bid/ask ladders from 'book' (snapshot) and 'price_change' (level updates) messages."""

    def __init__(self, tokens):
        self.tokens = set(tokens)
        self.bids: dict[str, dict[float, float]] = {}
        self.asks: dict[str, dict[float, float]] = {}
        self.last_top: dict[str, tuple] = {}
        self.mismatch = 0

    def top(self, tok):
        b, a = self.bids.get(tok), self.asks.get(tok)
        bb = max(b) if b else None
        ba = min(a) if a else None
        return (bb, b[bb] if b else None, ba, a[ba] if a else None)

    @staticmethod
    def _levels(lst):
        out = {}
        for lv in lst or []:
            try:
                p, s = float(lv["price"]), float(lv["size"])
            except (KeyError, TypeError, ValueError):
                continue
            if s > 0:
                out[p] = s
        return out

    def invalidate(self):
        """Disconnect: drop every ladder; returns tokens that had a valid book."""
        gone = [t for t in self.last_top]
        self.bids.clear(); self.asks.clear(); self.last_top.clear()
        return gone

    def feed(self, obj, recv_ns):
        """obj = one parsed message dict. Returns [(token, recv_ns, ts_ms, top)] for tokens whose top CHANGED."""
        et = obj.get("event_type")
        ts = obj.get("timestamp")
        try:
            ts = int(ts) if ts is not None else None
        except (TypeError, ValueError):
            ts = None
        touched = []
        if et == "book":
            tok = obj.get("asset_id")
            if tok in self.tokens:
                self.bids[tok] = self._levels(obj.get("bids"))
                self.asks[tok] = self._levels(obj.get("asks"))
                touched.append(tok)
        elif et == "price_change":
            checks = []
            for ch in obj.get("price_changes") or []:
                tok = ch.get("asset_id")
                if tok not in self.tokens or tok not in self.bids:       # need a snapshot first
                    continue
                try:
                    p, s = float(ch["price"]), float(ch["size"])
                except (KeyError, TypeError, ValueError):
                    continue
                side = self.bids[tok] if str(ch.get("side")).upper() == "BUY" else self.asks[tok]
                if s > 0:
                    side[p] = s
                else:
                    side.pop(p, None)
                touched.append(tok)
                checks.append((tok, ch))
            for tok, ch in checks:                 # best_bid/best_ask describe the book AFTER the whole message
                try:
                    t = self.top(tok)
                    mb, ma = ch.get("best_bid"), ch.get("best_ask")
                    if (mb not in (None, "") and t[0] is not None and abs(float(mb) - t[0]) > 1e-9) or                             (ma not in (None, "") and t[2] is not None and abs(float(ma) - t[2]) > 1e-9):
                        self.mismatch += 1
                    # Heal: the server's best price is authoritative; any local level strictly better than it is stale.
                    # (0 / 1 are the server's "empty side" sentinels - never heal against those.)
                    if mb not in (None, "") and 0 < float(mb) < 1:
                        for q in [q for q in self.bids[tok] if q > float(mb) + 1e-9]:
                            del self.bids[tok][q]
                    if ma not in (None, "") and 0 < float(ma) < 1:
                        for q in [q for q in self.asks[tok] if q < float(ma) - 1e-9]:
                            del self.asks[tok][q]
                except (TypeError, ValueError):
                    pass
        out = []
        for tok in dict.fromkeys(touched):
            cur = self.top(tok)
            if self.last_top.get(tok) != cur:
                self.last_top[tok] = cur
                out.append((tok, recv_ns, ts, cur))
        return out


# ------------------------------------------------------------------ discovery (REST, GET only)
def kalshi_discover(date: str) -> dict:
    """{game: {game, away, home, ml:{code:{ticker,title,sub}}, spreads:[{ticker,code,floor,title,strike_type}], events}}"""
    from kpm.clients.kalshi import KalshiClient
    cl = KalshiClient()
    games: dict[str, dict] = {}
    for series in ("KXNBAGAME", "KXNBASPREAD"):
        cur = None
        while True:
            kw = dict(series_ticker=series, status="open", limit=200, with_nested_markets=True)
            if cur:
                kw["cursor"] = cur
            r = cl.http.get("/events", kw, use_cache=False, persist=False)
            for e in r.get("events", []):
                g, d, away, home = split_game(e["event_ticker"])
                if d != date or not away:
                    continue
                kg = games.setdefault(g, dict(game=g, away=away, home=home, ml={}, spreads=[], events={}))
                kg["events"][series] = e["event_ticker"]
                for m in e.get("markets", []):
                    if m.get("status") in DEAD_STATUS:
                        continue
                    code = kalshi_code(m["ticker"], series)
                    if series == "KXNBAGAME" and code:
                        kg["ml"][code] = dict(ticker=m["ticker"], title=m.get("title"), sub=m.get("yes_sub_title"))
                    elif series == "KXNBASPREAD" and code:
                        kg["spreads"].append(dict(ticker=m["ticker"], code=code, floor=m.get("floor_strike"),
                                                  title=m.get("title"), strike_type=m.get("strike_type")))
            cur = r.get("cursor")
            if not cur:
                break
    return games


def gamma_event(slug: str):
    """GET /events/slug/<slug> -> dict or None (404 / empty)."""
    req = urllib.request.Request(f"{GAMMA}/events/slug/{slug}", headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            doc = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    return doc if isinstance(doc, dict) and doc.get("markets") else None


def build_map(date: str, tag: str, fetch=gamma_event, kgames=None) -> dict:
    kgames = kalshi_discover(date) if kgames is None else kgames
    ymd = datetime.strptime(date, "%y%b%d").strftime("%Y-%m-%d")
    doc = dict(date=date, tag=tag, built=lf.iso(time.time()), games={}, pairs=[], unmatched=[], misses=[], kalshi_only=[])
    for g, kg in sorted(kgames.items()):
        info = dict(away=kg["away"], home=kg["home"], kalshi_events=kg["events"], n_ml=len(kg["ml"]),
                    n_spreads=len(kg["spreads"]), slug=None, poly_title=None)
        gev = None
        for a, h in ((kg["away"], kg["home"]), (kg["home"], kg["away"])):
            slug = f"nba-{a.lower()}-{h.lower()}-{ymd}"
            try:
                gev = fetch(slug)
            except Exception as exc:
                doc["misses"].append(dict(game=g, slug=slug, err=type(exc).__name__))
                continue
            if gev:
                info["slug"], info["poly_title"] = slug, gev.get("title")
                break
            doc["misses"].append(dict(game=g, slug=slug, err="404/empty"))
        doc["games"][g] = info
        if not gev:
            continue
        pairs, unm = match_game(kg, gev)
        doc["pairs"] += pairs
        doc["unmatched"] += unm
        used = {p["kalshi"] for p in pairs}
        doc["kalshi_only"] += [t for t in [v["ticker"] for v in kg["ml"].values()] + [s["ticker"] for s in kg["spreads"]]
                               if t not in used]
    return doc


# ------------------------------------------------------------------ recorder
class Recorder:
    def __init__(self, doc, env, until_ts, out_dir: Path, name: str, date: str, tag: str):
        self.doc, self.env, self.until_ts = doc, env, until_ts
        self.out_dir, self.name, self.date, self.tag = out_dir, name, date, tag
        self.ktickers = sorted({p["kalshi"] for p in doc["pairs"]})
        self.ptokens = sorted({p["poly_token"] for p in doc["pairs"]})
        self.bs = lf.make_fast_bookset()
        self.pb = PolyBooks(self.ptokens)
        self.fh = None
        self.n_msgs = dict(k=0, p=0)
        self.n_tops = dict(k=0, p=0)
        self.reconnects = dict(k=0, p=0)
        self.errors = dict(k=0, p=0)
        self.last_msg_ns = dict(k=None, p=None)
        self.last_err = None
        self.seq_gaps = 0
        self.connected = dict(k=False, p=False)
        self._cid = 0
        self._pending, self._subs, self._last_seq = {}, {}, {}

    # ---- output
    def w(self, obj):
        self.fh.write(lf.dumps(obj) + "\n")

    def ev(self, kind, v=None, **kw):
        self.w(dict(ev=kind, v=v, r=time.time_ns(), **kw))

    def stop_now(self):
        return time.time() >= self.until_ts or (self.out_dir / (self.name + ".STOP")).exists()

    def err(self, v, where, exc):
        self.errors[v] += 1
        self.last_err = f"{v}:{where}:{type(exc).__name__}"
        self.ev("error", v, where=where, exc=type(exc).__name__)

    async def flusher(self):
        while not self.stop_now():
            await asyncio.sleep(FLUSH_S)
            self.fh.flush()

    def heartbeat(self):
        now_ns = time.time_ns()
        age = lambda v: None if self.last_msg_ns[v] is None else round((now_ns - self.last_msg_ns[v]) / 1e9, 2)
        doc = dict(time=lf.iso(time.time()), date=self.date, tag=self.tag, msgs=dict(self.n_msgs), top_records=dict(self.n_tops),
                   kalshi_books_valid=len(self.bs.sid_of), kalshi_tickers=len(self.ktickers),
                   poly_books_valid=len(self.pb.last_top), poly_tokens=len(self.ptokens),
                   connected=dict(self.connected), reconnects=dict(self.reconnects), errors=dict(self.errors),
                   last_error=self.last_err, last_update_age_s=dict(k=age("k"), p=age("p")),
                   kalshi_seq_gaps=self.seq_gaps, poly_top_mismatch=self.pb.mismatch,
                   json_impl=lf.JSON_IMPL, loop=lf.LOOP_IMPL)
        tmp = self.out_dir / (self.name + ".heartbeat.tmp")
        tmp.write_text(json.dumps(doc), encoding="utf-8")
        tmp.replace(self.out_dir / (self.name + ".heartbeat"))
        print(f"{doc['time']} hb msgs={doc['msgs']} tops={doc['top_records']} valid k={doc['kalshi_books_valid']}/"
              f"{doc['kalshi_tickers']} p={doc['poly_books_valid']}/{doc['poly_tokens']} age={doc['last_update_age_s']} "
              f"reconn={doc['reconnects']} err={doc['errors']}", flush=True)

    async def hb_loop(self):
        while not self.stop_now():
            self.heartbeat()
            for _ in range(int(HB_S)):
                if self.stop_now():
                    return
                await asyncio.sleep(1.0)

    # ---- Kalshi
    async def ksend(self, ws, cmd, params):
        assert cmd in ("subscribe", "unsubscribe")
        self._cid += 1
        await ws.send(json.dumps({"id": self._cid, "cmd": cmd, "params": params}))
        return self._cid

    async def ksubscribe(self, ws, tickers):
        for part in lf.chunks(tickers):
            cid = await self.ksend(ws, "subscribe", {"channels": ["orderbook_delta"], "market_tickers": part})
            self._pending[cid] = part

    def k_invalid(self, tickers, recv_ns):
        for t in tickers:
            self.bs.last_top.pop(t, None)
            self.w(dict(v="k", t=t, r=recv_ns, inv=1))

    async def khandle(self, ws, raw, recv_ns):
        self.n_msgs["k"] += 1
        self.last_msg_ns["k"] = recv_ns
        try:
            msg = lf.loads(raw)
        except (ValueError, TypeError):
            return
        if not isinstance(msg, dict):
            return
        mt = msg.get("type")
        body = msg.get("msg") if isinstance(msg.get("msg"), dict) else {}
        if mt == "subscribed":
            pend = self._pending.pop(msg.get("id"), None)
            if pend and isinstance(body.get("sid"), int):
                self._subs[body["sid"]] = list(pend)
                self._last_seq.pop(body["sid"], None)
        elif mt == "error":
            self._pending.pop(msg.get("id"), None)
            self.errors["k"] += 1
            self.last_err = "k:server:" + json.dumps(body)[:160]
            self.ev("error", "k", where="server", detail=json.dumps(body)[:200])
        sid, seq = msg.get("sid"), msg.get("seq")
        gap, last = False, None
        if isinstance(sid, int) and isinstance(seq, int):
            last = self._last_seq.get(sid)
            self._last_seq[sid] = seq
            gap = last is not None and seq != last + 1 and sid in self._subs
        ts_ms = body.get("ts_ms")
        for top in list(self.bs.feed({"recv_ns": recv_ns, "msg_obj": msg})):
            if not hasattr(top, "best_yes_bid"):
                continue                                          # Trade objects are not recorded
            self.n_tops["k"] += 1
            self.w(dict(v="k", t=top.ticker, r=top.recv_ns, x=ts_ms, b=top.best_yes_bid, bs=top.bid_size,
                        a=top.best_yes_ask, **{"as": top.ask_size}))
        if self.bs.dropped:
            self.k_invalid(list(dict.fromkeys(self.bs.dropped)), recv_ns)
            self.bs.dropped.clear()
        if gap:
            self.seq_gaps += 1
            tk = self._subs.pop(sid)
            self._last_seq.pop(sid, None)
            self.ev("seq_gap", "k", sid=sid, expected=last + 1, got=seq, n_markets=len(tk))
            await self.ksend(ws, "unsubscribe", {"sids": [sid]})
            await self.ksubscribe(ws, tk)

    async def ksession(self):
        import websockets
        from kpm.execution.auth import WS_SIGN_PATH, auth_headers
        self._pending.clear(); self._subs.clear(); self._last_seq.clear()
        hdr = auth_headers(self.env.key, self.env.key_id, "GET", WS_SIGN_PATH)      # re-signed on every connect
        try:
            conn = websockets.connect(self.env.ws_url, additional_headers=hdr)
        except TypeError:                                                           # websockets < 14
            conn = websockets.connect(self.env.ws_url, extra_headers=hdr)
        before = self.n_msgs["k"]
        async with conn as ws:
            self.connected["k"] = True
            self.ev("connect", "k", tickers=len(self.ktickers))
            self.bs.feed({"recv_ns": time.time_ns(), "ctl": {"event": "connect"}})
            self.bs.dropped.clear()
            await self.ksubscribe(ws, self.ktickers)
            while not self.stop_now():
                try:
                    raw = await asyncio.wait_for(ws.recv(), 1.0)
                except asyncio.TimeoutError:
                    continue
                await self.khandle(ws, raw, time.time_ns())
        return self.n_msgs["k"] - before

    async def krun(self):
        if not self.ktickers:
            return
        delay = 1.0
        while not self.stop_now():
            try:
                got = await self.ksession()
                if got:
                    delay = 1.0
                why = "closed"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.err("k", "session", exc)
                why = type(exc).__name__
            self.connected["k"] = False
            ns = time.time_ns()
            self.bs.feed({"recv_ns": ns, "ctl": {"event": "disconnect"}})
            self.k_invalid(list(dict.fromkeys(self.bs.dropped)), ns)
            self.bs.dropped.clear()
            self.ev("disconnect", "k", why=why)
            if self.stop_now():
                break
            self.reconnects["k"] += 1
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)

    # ---- Polymarket
    def phandle(self, raw, recv_ns):
        self.n_msgs["p"] += 1
        self.last_msg_ns["p"] = recv_ns
        if raw in ("PONG", b"PONG"):
            return
        try:
            obj = lf.loads(raw)
        except (ValueError, TypeError):
            return
        for m in (obj if isinstance(obj, list) else [obj]):
            if not isinstance(m, dict):
                continue
            for tok, r, ts, top in self.pb.feed(m, recv_ns):
                self.n_tops["p"] += 1
                self.w(dict(v="p", t=tok, r=r, x=ts, b=top[0], bs=top[1], a=top[2], **{"as": top[3]}))

    async def ping_loop(self, ws):
        while True:
            await asyncio.sleep(POLY_PING_S)
            await ws.send("PING")

    async def psession(self):
        import websockets
        before = self.n_msgs["p"]
        async with websockets.connect(POLY_WS, ping_interval=None, max_size=None) as ws:
            self.connected["p"] = True
            self.ev("connect", "p", tokens=len(self.ptokens))
            await ws.send(json.dumps({"assets_ids": self.ptokens, "type": "market"}))   # initial subscribe; book snapshots follow
            pinger = asyncio.create_task(self.ping_loop(ws))
            try:
                last_rx = time.time()
                while not self.stop_now():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), 1.0)
                    except asyncio.TimeoutError:
                        if time.time() - last_rx > POLY_IDLE_RECONNECT_S:
                            raise TimeoutError("no frames (not even PONG) for 30 s")
                        continue
                    last_rx = time.time()
                    self.phandle(raw, time.time_ns())
            finally:
                pinger.cancel()
        return self.n_msgs["p"] - before

    async def prun(self):
        if not self.ptokens:
            return
        delay = 1.0
        while not self.stop_now():
            try:
                got = await self.psession()
                if got:
                    delay = 1.0
                why = "closed"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.err("p", "session", exc)
                why = type(exc).__name__
            self.connected["p"] = False
            ns = time.time_ns()
            for t in self.pb.invalidate():
                self.w(dict(v="p", t=t, r=ns, inv=1))
            self.ev("disconnect", "p", why=why)
            if self.stop_now():
                break
            self.reconnects["p"] += 1
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)

    async def run(self):
        tasks = [asyncio.create_task(c) for c in (self.krun(), self.prun(), self.hb_loop(), self.flusher())]
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.ev("stop")
            self.fh.flush()
            self.heartbeat()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--date", required=True, help="event date code, e.g. 26OCT21")
    ap.add_argument("--until", default=None, help="ISO time or +60s/+5m; default 09:00Z the day after --date")
    ap.add_argument("--out-dir", default=str(ROOT / "logs"))
    ap.add_argument("--tag", default="home")
    args = ap.parse_args()
    date = args.date.upper()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"pairrec_{date}_{args.tag}"

    from kpm.execution.env import load_env
    env = load_env(require_keys=True, expected="prod")

    doc = build_map(date, args.tag)
    (out_dir / (name + ".map.json")).write_text(json.dumps(doc, indent=1), encoding="utf-8")
    pm = [p for p in doc["pairs"] if p["type"] == "moneyline"]
    ps = [p for p in doc["pairs"] if p["type"] == "spread"]
    n_poly = sum(1 for g in doc["games"].values() if g["slug"])
    print(f"pairrec {date}/{args.tag}: kalshi games={len(doc['games'])} polymarket games={n_poly} "
          f"pairs moneyline={len(pm)} spread={len(ps)} unmatched={len(doc['unmatched'])} "
          f"kalshi_only={len(doc['kalshi_only'])} slug_misses={len(doc['misses'])}", flush=True)
    for m in doc["misses"]:
        print("  slug miss:", m)
    for u in doc["unmatched"]:
        if u.get("type") == "moneyline" or "positive spread" not in u.get("why", ""):
            print(f"  unmatched [{u['game']}] {u['poly_question']!r}: {u['why']}")
    n_pos = sum(1 for u in doc["unmatched"] if "positive spread" in u.get("why", ""))
    if n_pos:
        print(f"  ({n_pos} positive-line spread markets not recorded: complements of the opponent's Kalshi market)")
    if not doc["pairs"]:
        print("no mapped pairs; nothing to record", file=sys.stderr)
        return 2

    until_ts = lf.parse_until(args.until, date)
    lf.LOOP_IMPL = lf.install_uvloop()
    rec = Recorder(doc, env, until_ts, out_dir, name, date, args.tag)
    rec.fh = open(out_dir / (name + ".jsonl"), "a", encoding="utf-8")
    rec.ev("start", date=date, tag=args.tag, kalshi_tickers=len(rec.ktickers), poly_tokens=len(rec.ptokens),
           until=lf.iso(until_ts), platform=platform.platform(), python=platform.python_version(),
           json_impl=lf.JSON_IMPL, loop=lf.LOOP_IMPL)
    print(f"recording {len(rec.ktickers)} Kalshi tickers + {len(rec.ptokens)} Polymarket tokens until {lf.iso(until_ts)}; "
          f"stop via {out_dir / (name + '.STOP')}", flush=True)
    try:
        asyncio.run(rec.run())
    except KeyboardInterrupt:
        print("interrupted")
    finally:
        rec.fh.close()
    print(f"done: msgs={rec.n_msgs} top_records={rec.n_tops} reconnects={rec.reconnects} errors={rec.errors} "
          f"seq_gaps={rec.seq_gaps} poly_top_mismatch={rec.pb.mismatch}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
