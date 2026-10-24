"""READ-ONLY latency measurement. GET exchange/status, SNTP, signing timing, WS orderbook_delta subscribe only.
Run from repo root: KALSHI_ENV=prod .venv/Scripts/python.exe reports/output/latency/measure.py [--secs 120]"""
from __future__ import annotations
import argparse, asyncio, http.client, json, socket, ssl, statistics as st, struct, sys, time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PICKS = ROOT / "reports/output/nba_picks/26OCT20"
sys.path.insert(0, str(ROOT / "src"))
REST_HOST = "api.elections.kalshi.com"
PFX = ("KXNBAGAME", "KXNBASPREAD", "KXNBATOTAL")


def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(round(p / 100 * (len(v) - 1))))] if v else None


def summ(v):
    if not v:
        return {"n": 0}
    return {"n": len(v), "min": min(v), "p10": pct(v, 10), "median": st.median(v), "p90": pct(v, 90),
            "p99": pct(v, 99), "max": max(v), "mean": st.fmean(v)}


def sntp(host, timeout=3.0):
    pkt = b"\x1b" + 47 * b"\0"
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.connect((host, 123))
        t0 = time.time(); s.send(pkt); d = s.recv(48); t3 = time.time()
    finally:
        s.close()
    f = struct.unpack("!12I", d)
    conv = lambda sec, fr: sec - 2208988800 + fr / 2**32
    t1, t2 = conv(f[8], f[9]), conv(f[10], f[11])
    return ((t1 - t0) + (t2 - t3)) / 2 * 1000, ((t3 - t0) - (t2 - t1)) * 1000   # offset ms (true - local), rtt ms


def do_ntp():
    out, allo = {}, []
    for h in ("time.windows.com", "time.google.com", "pool.ntp.org"):
        offs, rtts = [], []
        for _ in range(5):
            try:
                o, r = sntp(h); offs.append(o); rtts.append(r)
            except Exception as e:
                out.setdefault(h, {})["err"] = type(e).__name__
            time.sleep(0.2)
        if offs:
            out[h] = {"n": len(offs), "offset_ms_median": st.median(offs), "offset_ms_all": [round(x, 2) for x in offs],
                      "rtt_ms_median": st.median(rtts), "rtt_ms_min": min(rtts)}
            allo += [o for _, o in sorted(zip(rtts, offs))[:3]]   # lowest-RTT samples
    return out, (st.median(allo) if allo else 0.0)


def rest():
    hosts = {}
    for h in (REST_HOST, "external-api-ws.kalshi.com", "trading-api.kalshi.com"):
        try:
            hosts[h] = sorted({a[4][0] for a in socket.getaddrinfo(h, 443, socket.AF_INET)})
        except Exception as e:
            hosts[h] = [type(e).__name__]
    ctx = ssl.create_default_context()
    path = "/trade-api/v2/exchange/status"
    warm, status, hdrs = [], None, {}
    c = http.client.HTTPSConnection(REST_HOST, context=ctx, timeout=10)
    for i in range(31):
        t = time.perf_counter(); c.request("GET", path, headers={"Accept": "application/json"})
        r = c.getresponse(); r.read(); dt = (time.perf_counter() - t) * 1000
        status = r.status
        if i == 1:
            hdrs = {k: r.getheader(k) for k in ("Via", "X-Cache", "X-Amz-Cf-Pop", "Server") if r.getheader(k)}
        if i:   # first request pays TCP+TLS; excluded from warm set
            warm.append(dt)
    c.close()
    cold = []
    for _ in range(5):
        t = time.perf_counter()
        c = http.client.HTTPSConnection(REST_HOST, context=ctx, timeout=10)
        c.request("GET", path); r = c.getresponse(); r.read()
        cold.append((time.perf_counter() - t) * 1000); c.close()
        time.sleep(0.3)
    return {"http_status": status, "resolved": hosts, "resp_headers": hdrs,
            "warm_ms (30 reqs, kept-alive)": summ(warm), "cold_ms (5, fresh TCP+TLS)": summ(cold)}


def signing(env):
    from kpm.execution.auth import auth_headers
    v = []
    for _ in range(1000):
        t = time.perf_counter(); auth_headers(env.key, env.key_id, "GET", "/trade-api/v2/exchange/status")
        v.append((time.perf_counter() - t) * 1000)
    return {"calls": 1000, "mean_ms": st.fmean(v), "median_ms": st.median(v), "p99_ms": pct(v, 99), "max_ms": max(v)}


def exch_ts_ms(msg):
    body = msg.get("msg") or {}
    if "ts_ms" in body:
        return float(body["ts_ms"])
    v = body.get("ts")
    if v is None:
        return None
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp() * 1000
        except ValueError:
            return None
    v = float(v)
    return v * 1000 if v < 1e11 else v


async def ws_phase(env, tickers, secs, offset_ms):
    import websockets
    from kpm.execution.auth import WS_SIGN_PATH, auth_headers
    hdr = auth_headers(env.key, env.key_id, "GET", WS_SIGN_PATH)
    delays, proc, types, keys = [], [], {}, {}
    books, n_delta, n_total, n_nots, cid = {}, 0, 0, 0, 0
    async with websockets.connect(env.ws_url, additional_headers=hdr) as ws:
        for i in range(0, len(tickers), 200):
            cid += 1
            await ws.send(json.dumps({"id": cid, "cmd": "subscribe",
                                      "params": {"channels": ["orderbook_delta"], "market_tickers": tickers[i:i + 200]}}))
        t_start = time.time()
        while time.time() - t_start < secs:
            try:
                raw = await asyncio.wait_for(ws.recv(), 1.0)
            except asyncio.TimeoutError:
                continue
            recv_ms = time.time_ns() / 1e6 + offset_ms
            p0 = time.perf_counter_ns()
            msg = json.loads(raw)
            mt = msg.get("type"); body = msg.get("msg") or {}
            if mt == "orderbook_snapshot":
                bk = books[body.get("market_ticker")] = {}
                for side in ("yes_dollars_fp", "no_dollars_fp", "yes", "no"):
                    for lv in body.get(side) or []:
                        bk[(side, lv[0])] = lv[1]
            elif mt == "orderbook_delta":
                bk = books.setdefault(body.get("market_ticker"), {})
                k = (body.get("side"), body.get("price_dollars", body.get("price")))
                bk[k] = bk.get(k, 0) + float(body.get("delta_fp", body.get("delta", 0)) or 0)
            p1 = time.perf_counter_ns()
            n_total += 1; types[mt] = types.get(mt, 0) + 1
            if mt == "orderbook_delta":
                n_delta += 1
                proc.append((p1 - p0) / 1e6)
                keys.setdefault("delta_msg_keys", sorted(msg)); keys.setdefault("delta_body_keys", sorted(body))
                e = exch_ts_ms(msg)
                if e is None:
                    n_nots += 1
                else:
                    delays.append(recv_ms - e)
        el = time.time() - t_start
    return {"tickers": len(tickers), "secs": round(el, 1), "msgs_total": n_total, "types": types, "msg_rate_per_s": n_total / el,
            "deltas": n_delta, "deltas_without_ts": n_nots, **keys,
            "feed_delay_ms": summ(delays), "negative_delays": sum(1 for d in delays if d < 0),
            "proc_ms_per_delta": {"mean": st.fmean(proc) if proc else None, "median": st.median(proc) if proc else None,
                                  "p99": pct(proc, 99), "max": max(proc) if proc else None}}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--secs", type=float, default=120)
    a = ap.parse_args()
    from kpm.execution.env import load_env
    env = load_env(require_keys=True, expected="prod")
    res = {"run_utc": datetime.now(timezone.utc).isoformat()}
    print("ntp...")
    res["ntp"], off = do_ntp(); res["ntp_offset_used_ms (true - local)"] = off
    print(f"clock offset (true - local) = {off:.1f} ms")
    print("rest..."); res["rest"] = rest()
    print("signing..."); res["signing"] = signing(env)
    tickers = []
    for g in ("bosdet", "phinyk", "okcsas"):
        tickers += [t for t in json.loads((PICKS / f"{g}.json").read_text())["books"] if t.startswith(PFX)]
    tickers = sorted(set(tickers))
    print(f"ws {len(tickers)} tickers {a.secs}s...")
    res["ws"] = asyncio.run(ws_phase(env, tickers, a.secs, off))
    (HERE / "results.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=1)[:7000])


if __name__ == "__main__":
    main()
