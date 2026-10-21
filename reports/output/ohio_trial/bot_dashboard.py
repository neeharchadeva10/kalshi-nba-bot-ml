"""READ-ONLY local dashboard for the Ohio lock bots: http://127.0.0.1:8767

  .venv/Scripts/python.exe reports/output/ohio_trial/bot_dashboard.py --date 26OCT20

Every few seconds it reads the bots' heartbeat, ledger and trade-attempt lines from the server over SSH (cat/grep on
log files only) and serves one auto-refreshing page. It never sends anything to Kalshi and has no order code.
"""
import argparse
import html
import json
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SSH = r"C:\Windows\System32\OpenSSH\ssh.exe"
KEY = str(Path.home() / ".ssh" / "kalshi-ohio.pem")
HOST = "ubuntu@18.223.21.99"
STATE = {"fetched": None, "error": None, "hb": {}, "ledger": None, "events": []}
EV_RE = r'"event": ?"(attempt|stop|bot_error|attempt_error|cap_block|start)"'


def fetch(date: str) -> None:
    cmd = (f"cd ~/kpm/logs && for f in lockbot_{date}_*.heartbeat; do [ -e \"$f\" ] || continue; "
           f"case \"$f\" in *dry*) continue;; esac; echo \"##HB $f\"; cat \"$f\"; echo; done; "
           f"echo '##LEDGER'; cat lockbot_{date}_ledger.json 2>/dev/null; echo; "
           f"echo '##EVENTS'; grep -h -E '{EV_RE}' $(ls lockbot_{date}_*.jsonl | grep -v dry) 2>/dev/null | tail -200")
    out = subprocess.run([SSH, "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-i", KEY, HOST, cmd],
                         capture_output=True, text=True, timeout=25)
    if out.returncode != 0 and not out.stdout:
        raise RuntimeError((out.stderr or "ssh failed").strip()[:200])
    hb, ledger, events, section, cur = {}, None, [], None, None
    for line in out.stdout.splitlines():
        if line.startswith("##HB "):
            section, cur = "hb", line[5:].replace(f"lockbot_{date}_", "").replace(".heartbeat", "")
            continue
        if line.startswith("##LEDGER"):
            section = "ledger"
            continue
        if line.startswith("##EVENTS"):
            section = "events"
            continue
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if section == "hb":
            hb[cur] = d
        elif section == "ledger":
            ledger = d
        elif section == "events":
            events.append(d)
    STATE.update(fetched=time.time(), error=None, hb=hb, ledger=ledger, events=events)


def poller(date: str, every: float) -> None:
    while True:
        try:
            fetch(date)
        except Exception as exc:                                   # keep serving the last good data
            STATE["error"] = f"{type(exc).__name__}: {exc}"
        time.sleep(every)


def e(x) -> str:
    return html.escape("" if x is None else str(x))


def money(x) -> str:
    try:
        return f"${float(x):.2f}"
    except (TypeError, ValueError):
        return "-"


def page() -> str:
    now = time.time()
    age = None if STATE["fetched"] is None else now - STATE["fetched"]
    rows_hb = []
    for game, d in sorted(STATE["hb"].items()):
        hb_age = None
        try:
            hb_age = now - datetime.fromisoformat(d["time"].replace("Z", "+00:00")).timestamp()
        except (KeyError, ValueError):
            pass
        alive = hb_age is not None and hb_age < 90
        status = "HALTED: " + e(d.get("halted")) if d.get("halted") else ("LIVE" if alive else "NO HEARTBEAT")
        cls = "bad" if (d.get("halted") or not alive) else "ok"
        rows_hb.append(
            f"<tr><td>{e(game)}</td><td class='{cls}'>{status}</td><td>{e(d.get('mode'))}</td>"
            f"<td>{d.get('messages', 0):,}</td><td>{e(d.get('valid_books'))}/{e(d.get('tickers'))}</td>"
            f"<td>{e(d.get('locks_opened_total'))}</td><td>{e(d.get('eligible'))}</td><td><b>{e(d.get('attempts'))}</b></td>"
            f"<td>{e(d.get('complete'))}</td><td>{e(d.get('partial'))}</td><td>{e(d.get('none'))}</td>"
            f"<td>{e(d.get('unknown'))}</td><td>{e(d.get('order_errors'))}</td>"
            f"<td>{money(d.get('game_at_risk'))} / {money(d.get('game_cap'))}</td>"
            f"<td>{'-' if hb_age is None else f'{hb_age:.0f}s'}</td></tr>")
    attempts = [d for d in STATE["events"] if d.get("event") == "attempt" and d.get("mode") == "live"]
    profit = sum(float(d.get("guaranteed_payout") or 0) - float(d.get("realized_cost") or 0)
                 for d in attempts if d.get("summary") == "complete")
    at_risk = sum(float(d.get("at_risk") or 0) for d in attempts)
    rows_tr = []
    for d in reversed(attempts[-100:]):
        legs = "<br>".join(
            f"{e(l.get('side')).upper()} {e(l.get('ticker'))} &rarr; filled {e(l.get('fill_count'))}"
            f"{' @ ' + e(l.get('avg_price')) if l.get('avg_price') else ''}"
            f"{' <span class=bad>' + e(l.get('error')) + '</span>' if l.get('error') else ''}"
            for l in d.get("legs", []))
        cls = {"complete": "ok", "partial": "bad", "unknown": "bad"}.get(d.get("summary"), "")
        lat = d.get("decision_latency_us")
        rows_tr.append(
            f"<tr><td>{e((d.get('time') or '')[11:23])}</td><td>{e(d.get('kind'))}</td>"
            f"<td class='{cls}'>{e(d.get('summary'))}</td><td>{legs}</td><td>{money(d.get('realized_cost'))}</td>"
            f"<td>{money(d.get('guaranteed_payout'))}</td><td>{money(d.get('at_risk'))}</td>"
            f"<td>{'-' if lat is None else f'{lat / 1000:.2f} ms'}</td></tr>")
    stops = [d for d in STATE["events"] if d.get("event") in ("stop", "bot_error", "attempt_error", "cap_block")]
    stop_html = "".join(f"<li>{e((d.get('time') or '')[11:19])} {e(d.get('event'))}: "
                        f"{e(d.get('reason') or d.get('msg') or d.get('cap'))}</li>" for d in stops[-10:])
    night = (STATE["ledger"] or {}).get("settled") if isinstance(STATE["ledger"], dict) else None
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="refresh" content="5">
<title>Lock Bot Live</title><style>
:root{{--bg:#0f1115;--fg:#e6e6e6;--mut:#9aa0a6;--ok:#4caf50;--bad:#ef5350;--line:#2a2e36}}
body{{background:var(--bg);color:var(--fg);font:14px/1.4 system-ui,Segoe UI,Arial;margin:16px}}
h1{{font-size:20px;margin:0 0 4px}} .mut{{color:var(--mut)}} table{{border-collapse:collapse;width:100%;margin:8px 0 20px}}
th,td{{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}} th{{color:var(--mut);font-weight:600}}
.ok{{color:var(--ok);font-weight:600}} .bad{{color:var(--bad);font-weight:600}}
.kpi{{display:inline-block;margin:6px 24px 6px 0}} .kpi b{{font-size:22px;display:block}}
</style></head><body>
<h1>Lock bots — Ohio server (read-only view)</h1>
<div class="mut">Updated {'-' if age is None else f'{age:.0f}s ago'} · page refreshes every 5 s
{(' · <span class=bad>fetch error: ' + e(STATE['error']) + '</span>') if STATE['error'] else ''}</div>
<div><span class="kpi"><b>{len(attempts)}</b>live trades tried</span>
<span class="kpi"><b>{sum(1 for d in attempts if d.get('summary') == 'complete')}</b>both sides filled</span>
<span class="kpi"><b>{sum(1 for d in attempts if d.get('summary') == 'partial')}</b>one side only</span>
<span class="kpi"><b>{money(profit)}</b>locked profit (complete)</span>
<span class="kpi"><b>{money(at_risk)}</b>money at risk</span></div>
<h2>Bots</h2><table><tr><th>Game</th><th>Status</th><th>Mode</th><th>Updates</th><th>Books</th><th>Locks seen</th>
<th>Worth trading</th><th>Tried</th><th>Complete</th><th>One side</th><th>Nothing</th><th>Unclear</th><th>Order errors</th>
<th>At risk / cap</th><th>Heartbeat</th></tr>{''.join(rows_hb) or '<tr><td colspan=15 class=mut>no live bot heartbeats yet</td></tr>'}</table>
<h2>Trades (newest first)</h2><table><tr><th>Time (UTC)</th><th>Type</th><th>Result</th><th>Legs</th><th>Cost</th>
<th>Pays at least</th><th>At risk</th><th>Reaction</th></tr>{''.join(rows_tr) or '<tr><td colspan=8 class=mut>no trades yet</td></tr>'}</table>
<h2>Stops and errors</h2><ul>{stop_html or '<li class=mut>none</li>'}</ul>
<div class="mut">Night ledger (settled at-risk by game): {e(night)}</div>
</body></html>"""


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = page().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now(timezone.utc).strftime("%y%b%d").upper())
    ap.add_argument("--port", type=int, default=8767)
    ap.add_argument("--every", type=float, default=4.0)
    a = ap.parse_args()
    if not re.fullmatch(r"\d\d[A-Z]{3}\d\d", a.date):
        raise SystemExit("--date like 26OCT20")
    threading.Thread(target=poller, args=(a.date, a.every), daemon=True).start()
    print(f"dashboard: http://127.0.0.1:{a.port}  (date {a.date}, read-only)", flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()


if __name__ == "__main__":
    main()
