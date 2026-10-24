"""#49 step 2: hourly candles T-4h..T-1h for KXNBATEAMTOTAL and KXNBA1HTOTAL strikes within +-8 of the expected value
(team: M_game/2 ; 1H: 0.5*M_game, M_game = KXNBATOTAL T-1h median from #37's cache).  Public GET only, adaptive pacing
(0.4 req/s start, AIMD), raw responses cached in data/raw/kalshi_teamtotal_1h_hourly/, resumable, ETA in download_progress.log.

Usage:  .venv/Scripts/python.exe reports/output/nba_internal_mismatch/download_candles.py {discovery|unseen|all} [--plan]
  --plan : only count the jobs (no requests).
"""
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import data_io as D  # noqa: E402
import mismatch as MM  # noqa: E402
from pacing import PacedClient, ROOT  # noqa: E402

BASE = "https://api.elections.kalshi.com/trade-api/v2"
NS = ROOT / "data" / "raw" / "kalshi_teamtotal_1h_hourly"
LOG = HERE / "download_progress.log"


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(LOG, "a") as fh:
        fh.write(line + "\n")


def build_jobs(split):
    """Returns (jobs, skips). jobs = [(ticker, T, kind)] ; skips = Counter-like dict of game-level skip reasons."""
    mk = sqlite3.connect(HERE / "markets.sqlite")
    tc = D.totals_conn()
    jobs, skips, n_games = [], {}, 0

    def skip(r):
        skips[r] = skips.get(r, 0) + 1
    for ev in D.load_events(split):
        n_games += 1
        T = int(ev["T"])
        mg, _ = D.game_total_median(tc, ev["event"], T)
        if mg is None:
            skip("no_game_total_median_at_T-1h")
            continue
        m = D.markets_for(mk, ev["event"])
        if not m["team"] and not m["1H"]:
            skip("no_team_or_1H_markets_listed")
            continue
        for kind in ("team", "1H"):
            mkts = m[kind]
            if not mkts:
                skip(f"no_{kind}_markets_listed")
                continue
            ev_pts = MM.scope_ev(kind, mg)
            for row in mkts:
                tk, strike = (row[0], row[2]) if kind == "team" else (row[0], row[1])
                if MM.in_scope(strike, ev_pts):
                    jobs.append((tk, T, kind))
    return jobs, skips, n_games


def main():
    split = sys.argv[1] if len(sys.argv) > 1 else "discovery"
    plan = "--plan" in sys.argv
    jobs, skips, n_games = build_jobs(split)
    cdb = D.candle_db()
    done = {r[0] for r in cdb.execute("SELECT ticker FROM done WHERE note IN ('ok','http_404')")}
    todo = [j for j in jobs if j[0] not in done]
    log(f"split={split} games={n_games} game_skips={skips} jobs={len(jobs)} already_done={len(jobs) - len(todo)} todo={len(todo)}")
    if plan or not todo:
        return
    cl = PacedClient(BASE, NS, log=log)
    t0, k = time.time(), 0

    def px(c, side):
        v = (c.get(side) or {}).get("close")
        return float(v) if v not in (None, "") else None

    for tk, T, kind in todo:
        try:
            p = cl.get(f"/historical/markets/{tk}/candlesticks",
                       {"start_ts": T - 4 * 3600, "end_ts": T - 3600, "period_interval": 60})
        except Exception as e:  # noqa: BLE001
            cdb.execute("INSERT OR REPLACE INTO done VALUES (?,?,?,?)", (tk, T, 0, "error_" + type(e).__name__))
            log(f"ERROR {tk} {type(e).__name__}: {str(e)[:100]}")
            continue
        n = 0
        for c in p.get("candlesticks", []):
            if c["end_period_ts"] > T - 3600:          # guard: never keep a candle ending after the decision time
                continue
            v = c.get("volume")
            cdb.execute("INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?)", (
                tk, int(c["end_period_ts"]), px(c, "yes_bid"), px(c, "yes_ask"),
                float(v) if v not in (None, "") else None))
            n += 1
        cdb.execute("INSERT OR REPLACE INTO done VALUES (?,?,?,?)", (tk, T, n, "http_404" if p.get("_http_404") else "ok"))
        k += 1
        if k % 50 == 0:
            cdb.commit()
            el = time.time() - t0
            eta = el / k * (len(todo) - k)
            log(f"{k}/{len(todo)} elapsed {el:.0f}s ETA {eta:.0f}s ({eta / 3600:.2f}h) rate={cl.pacer.rate:.2f}/s stats={cl.stats}")
    cdb.commit()
    log(f"DONE {k} stats={cl.stats}")


if __name__ == "__main__":
    main()
