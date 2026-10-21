"""EXPLORATORY (research log #37-#39 follow-up): KXNBATEAMTOTAL data check. Public GETs via the cached research client,
NO outcomes analysis, NO candle download. Prints series fee info, market inventory, and a download-time estimate."""
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
from kpm.clients.kalshi import KalshiClient  # noqa: E402
from kpm.core.timeutil import parse_iso  # noqa: E402

SER = "KXNBATEAMTOTAL"
cl = KalshiClient(None)
s = cl.series(SER)
print("EXPLORATORY - not a test")
print("series keys:", sorted(s))
print("fee_type:", s.get("fee_type"), " fee_multiplier:", s.get("fee_multiplier"), " title:", s.get("title"))
mk = {}
for tier, hist in (("historical", True), ("live", False)):
    try:
        for m in cl.markets(SER, historical=hist):
            mk[m["ticker"]] = (tier, m)
    except Exception as e:  # noqa: BLE001
        print(tier, "listing failed:", repr(e)[:200])
print("markets total:", len(mk), Counter(t for t, _ in mk.values()))
ms = [m for _, m in mk.values()]
if ms:
    ex = sorted(ms, key=lambda m: m["ticker"])[len(ms) // 2]
    print("--- one market:", ex["ticker"], "| title:", ex.get("title"), "| strike_type:", ex.get("strike_type"),
          "| floor_strike:", ex.get("floor_strike"), "| cap:", ex.get("cap_strike"), "| status:", ex.get("status"))
    print("rules_primary:", ex.get("rules_primary"))
    print("rules_secondary:", ex.get("rules_secondary"))
    ev = defaultdict(list)
    for m in ms:
        ev[m["event_ticker"]].append(m)
    def d(m):
        return parse_iso(m.get("occurrence_datetime")) or parse_iso(m.get("expected_expiration_time"))
    tss = sorted(d(m) for m in ms if d(m))
    f = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")  # noqa: E731
    print("events:", len(ev), " first/last market occurrence:", f(tss[0]), f(tss[-1]))
    # 2025-26 season only (occurrence Oct 2025 - Aug 2026)
    se = {k: v for k, v in ev.items() if d(v[0]) and 1759276800 <= d(v[0]) < 1785542400}
    print("2025-26 events:", len(se), " markets:", sum(len(v) for v in se.values()))
    print("sample event tickers:", sorted(ev)[:6])
    cnt = Counter(len(v) for v in se.values())
    print("markets per event (2025-26) distribution:", dict(sorted(cnt.items())))
    fs = sorted({m.get("floor_strike") for m in ms if m.get("floor_strike") is not None})
    print("floor_strike examples:", fs[:6], "...", fs[-3:], " strike_types:", Counter(m.get("strike_type") for m in ms))
    # events per game: team-total events share the game code; count games = unique (date+teams) code
    games = Counter()
    for k in ev:
        code = k.split("-", 1)[1] if "-" in k else k
        games[code[:-3] if len(code) > 3 else code] += 1
    print("sample event suffixes:", [k.split('-',1)[1] for k in sorted(ev)[:4]])
    print("team-game events per game code (distribution):", dict(Counter(games.values())))
    nm = len(se and [m for v in se.values() for m in v])
    n_req = nm
    for rate in (2.5,):
        print(f"candle download (1 request/market, window T-4h..T-1h as #37): {n_req} requests at {rate}/s = "
              f"{n_req / rate / 60:.1f} min; if 2 requests per market: {2 * n_req / rate / 60:.1f} min")
