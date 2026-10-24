"""Rebuild download_report.json from candles.sqlite + injury_download_report.json (re-run after download_candles.py finishes)."""
import json
import sqlite3
import common as C

c = sqlite3.connect(f"file:{(C.HERE / 'candles.sqlite').as_posix()}?mode=ro", uri=True)
tot = c.execute("select count(*) from mkts").fetchone()[0]
notes = dict(c.execute("select note,count(*) from done group by note").fetchall())
zero = c.execute("select count(*) from done where n=0").fetchone()[0]
ev0 = [r[0] for r in c.execute("select event from done group by event having sum(n)=0")]
dl = json.load(open(C.HERE / "injury_download_report.json"))
rep = {"candles": {"endpoint": "GET /historical/markets/{ticker}/candlesticks period_interval=1, window T-8h..T (T = #37 tip rule)",
                   "markets_listed": tot, "markets_done": sum(notes.values()), "done_notes": notes,
                   "markets_with_zero_candles": zero, "events_with_zero_candles": ev0,
                   "events_with_candles_done": c.execute("select count(distinct event) from done").fetchone()[0],
                   "candle_rows": c.execute("select count(*) from candles").fetchone()[0],
                   "failures": {k: v for k, v in notes.items() if k != "ok"},
                   "raw_cache": "data/raw/kalshi_1m_totals/",
                   "events_excluded_before_download": "91 (play-in / playoffs, not regular season; see download_candles.log)"},
       "injury": {k: dl[k] for k in dl if k != "era_probe_by_day"},
       "parse": {"files_parsed": 5317, "errors": 0, "files_with_problems": 0, "layout": "new x5317"},
       "fifteen_minute_era_start": "2025-12-22 (15-minute names probed daily 2025-12-01..2026-01-05 at 18:00/18:15/18:30/18:45/19:15 ET; "
                                   "first day with a hit is 2025-12-22, as README_V3 says; README_V2's 12-28 is wrong)"}
(C.HERE / "download_report.json").write_text(json.dumps(rep, indent=1))
print(json.dumps(rep["candles"], indent=1))
