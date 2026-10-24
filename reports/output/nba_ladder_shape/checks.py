"""Research log #61 data checks, DISCOVERY dates only -> CHECKS.md.
1. T-1h quotes: 1-minute table vs hourly table per strike (median |mid diff|, share within 1c, strikes > 3c apart listed;
   their games are excluded from both tables in run_discovery / run_unseen via ls_pipeline.excluded_games).
2. Strike-result consistency: mkts.result == 'yes' iff hoopR final > strike.
Run: .venv/Scripts/python.exe reports/output/nba_ladder_shape/checks.py"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ls_common as C
import ls_pipeline as P


def main():
    ev = P.select_events(C.DISC_FIRST, C.DISC_LAST)           # date filter first
    assert ev.game_date_et.max() <= C.DISC_LAST
    rows = P.quote_check_rows(ev)
    diffs = np.array([abs(r["diff"]) for r in rows])
    bad = [r for r in rows if abs(r["diff"]) > C.QUOTE_TOL + 1e-9]
    excl = P.excluded_games(rows)
    L = ["# CHECKS (research log #61) - discovery dates only", "",
         f"Events in window {C.DISC_FIRST}..{C.DISC_LAST}: {len(ev)}", "",
         "## 1. T-1h quotes: 1-minute table vs hourly table", "",
         "Hourly candle = latest hourly candle ending in [D-30min, D], D = T - 60 min (ends exactly at D for T on the hour, at D-30min "
         "for T on the half hour). The 1-minute quote is the last 1-minute candle ending at or before THAT hourly end_ts.", "",
         f"- Strikes compared: {len(rows)} in {len({r['event'] for r in rows})} games",
         f"- Median |mid diff|: {np.median(diffs):.4f}" if len(diffs) else "- no comparisons",
         f"- Share within 1c: {float((diffs <= 0.01 + 1e-9).mean()):.4f}" if len(diffs) else "",
         f"- Share within 3c: {float((diffs <= C.QUOTE_TOL + 1e-9).mean()):.4f}" if len(diffs) else "",
         f"- Strikes more than 3c apart: {len(bad)}; games excluded from both tables: {len(excl)}", ""]
    if bad:
        L += ["| ticker | hourly end_ts | hourly mid | 1-min mid | diff | 1-min candle age (s) |", "|---|---|---|---|---|---|"]
        for r in sorted(bad, key=lambda r: r["ticker"]):
            L.append(f"| {r['ticker']} | {r['t_hourly']} | {r['mid_hourly']:.3f} | {r['mid_minute']:.3f} | {r['diff']:+.3f} | {r['age_minute']} |")
        L += ["", "Excluded games: " + ", ".join(excl), ""]
    # --- 2. strike-result consistency with hoopR finals (discovery outcomes only)
    mc = P.open_ro(C.INJ / "candles.sqlite")
    finals = P.load_finals([int(g) for g in ev.hoopr_gid.dropna()])
    n_ok = n_bad = n_nofinal = n_nores = 0
    mism = []
    for r in ev.itertuples():
        strikes, results = P.event_markets(mc, r.event)
        fin = finals.get(int(r.hoopr_gid)) if not np.isnan(r.hoopr_gid) else None
        for tk, K in strikes.items():
            res = results.get(tk)
            if fin is None:
                n_nofinal += 1
            elif res not in ("yes", "no"):
                n_nores += 1
            elif (res == "yes") == (fin > K):
                n_ok += 1
            else:
                n_bad += 1
                mism.append((r.event, tk, K, fin, res))
    mc.close()
    L += ["## 2. Strike-result consistency with hoopR finals", "",
          f"- consistent: {n_ok}; inconsistent: {n_bad}; strikes without a hoopR final (game unmatched): {n_nofinal}; "
          f"strikes without a yes/no result: {n_nores}", ""]
    if mism:
        L += ["| event | ticker | strike | hoopR final | mkts.result |", "|---|---|---|---|---|"]
        L += [f"| {e} | {t} | {k} | {f} | {x} |" for e, t, k, f, x in mism]
        L.append("")
    (C.HERE / "CHECKS.md").write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L[:30]))
    print("inconsistent strikes:", n_bad, "of", n_ok + n_bad)


if __name__ == "__main__":
    main()
