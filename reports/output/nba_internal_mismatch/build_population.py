"""#49 (amended 2026-10-22): the game population = every KXNBATOTAL event (2025-26) whose scheduled-start ET date is in
2026-02-01..2026-06-30 AND that has KXNBATEAMTOTAL and/or KXNBA1HTOTAL markets listed (markets.sqlite).  No prices or outcomes used.
Scheduled start T uses exactly #37's rule (drift occurrence-3h or own occurrence-3h; min with hoopR tip).  Ordered by (T, event);
discovery = first n//2 games, unseen = the rest.  Writes population.csv."""
import csv
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "reports/output/nba_totals_pace"))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))
import lib  # noqa: E402
import prep  # noqa: E402
import mismatch as MM  # noqa: E402

ET = ZoneInfo("America/New_York")
STYPE = {2: "regular", 3: "playoffs", 5: "playin"}


def main():
    tc = sqlite3.connect(f"file:{(ROOT / 'reports/output/nba_totals_pace/totals.sqlite').as_posix()}?mode=ro", uri=True)
    d = sqlite3.connect(f"file:{(ROOT / 'data/nba_drift.sqlite').as_posix()}?mode=ro", uri=True)
    start = {e.split("-", 1)[1]: o - 3 * 3600 for e, o in d.execute(
        "SELECT event_ticker, MIN(occurrence_ts) FROM markets GROUP BY event_ticker")}
    h = prep.hoopr_games(2026)
    mk = sqlite3.connect(HERE / "markets.sqlite")
    have = {r[0].split("-", 1)[1] for r in mk.execute("SELECT DISTINCT event_ticker FROM markets")}
    rows, skipped = [], {"no_T": 0, "outside_window": 0, "no_team_or_1H_listed": 0, "unparsed": 0, "season_2026_27": 0}
    for et, own in tc.execute("SELECT event_ticker, MIN(own_occ_ts) FROM markets GROUP BY event_ticker"):
        pe = lib.parse_event(et)
        if pe is None:
            skipped["unparsed"] += 1
            continue
        base = start.get(et.split("-", 1)[1])
        if base is None and own:
            base = own - 3 * 3600
        if base is None:
            skipped["no_T"] += 1
            continue
        if base >= prep.SEASON_SPLIT_TS:
            skipped["season_2026_27"] += 1
            continue
        T, st = base, None
        hm = prep.match_hoopr(h, *pe)
        if hm is not None:
            T, _ = prep.schedule_T(base, int(hm.tip))
            st = int(hm.season_type)
        dd = datetime.fromtimestamp(T, timezone.utc).astimezone(ET).strftime("%Y-%m-%d")
        if not (MM.WIN_FIRST <= dd <= MM.WIN_LAST):
            skipped["outside_window"] += 1
            continue
        if et.split("-", 1)[1] not in have:
            skipped["no_team_or_1H_listed"] += 1
            continue
        rows.append({"event": et, "game_date_et": dd, "away": pe[1], "home": pe[2], "T": T,
                     "season_type": STYPE.get(st, f"type{st}")})
    rows.sort(key=lambda r: (r["T"], r["event"]))
    nd = len(rows) // 2
    for r, sp in zip(rows, MM.split_by_order(len(rows))):
        r["split"] = sp
    with open(HERE / "population.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    from collections import Counter
    print("population", len(rows), "discovery", nd, "unseen", len(rows) - nd, "skipped", skipped)
    print(Counter((r["split"], r["season_type"]) for r in rows))
    print("discovery dates", rows[0]["game_date_et"], rows[nd - 1]["game_date_et"], "unseen", rows[nd]["game_date_et"], rows[-1]["game_date_et"])


if __name__ == "__main__":
    main()
