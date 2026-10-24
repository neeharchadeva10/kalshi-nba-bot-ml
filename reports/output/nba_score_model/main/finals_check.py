"""Research log #59, double check 3 (data agreement only; no model, no metric): hoopR final totals vs
  (a) Kalshi end-of-game value, 2025-26: per event the MAX expiration_value over the strikes that settled NO (reports/output/nba_totals_pace/
      totals.sqlite, opened read-only; the YES strikes carry the value at their early close, the NO strikes the final total);
  (b) OddsData 'Points', 2022-23, 2023-24 and 2024-25 (data/raw/nba_odds_samples/OddsData.sqlite, read-only; X and X_new tables).
Writes FINALS_CHECK.md (counts and every mismatch).  hoopR wins on any disagreement (nothing is changed).
Run:  .venv/Scripts/python.exe reports/output/nba_score_model/main/finals_check.py"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sm_lib as S  # noqa: E402
import clean as CL  # noqa: E402  (#57 impl A: matching, OddsData loader)

L = S.L
TOT = S.OUT / "nba_totals_pace" / "totals.sqlite"
ODDS = S.ROOT / "data" / "raw" / "nba_odds_samples" / "OddsData.sqlite"
MONTHS = {m: i + 1 for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}
EVENT = re.compile(r"^KXNBATOTAL-(\d\d)([A-Z]{3})(\d\d)([A-Z]{6})$")


def hoopr_games(years):
    """Every hoopR NBA-vs-NBA game of all season types in the file years: game_id, date (ET), home_f, away_f, season_type, total."""
    parts = []
    for y in years:
        tb = L.read_year(y)
        tb = tb[tb.team_abbreviation.isin(L.NBA_HOOPR) & tb.opponent_team_abbreviation.isin(L.NBA_HOOPR)]
        gdt = pd.to_datetime(tb.game_date_time)
        if gdt.dt.tz is None:
            gdt = gdt.dt.tz_localize("UTC")
        et = gdt.dt.tz_convert("America/New_York").dt.tz_localize(None).dt.normalize()
        h = pd.DataFrame({"game_id": tb.game_id.astype("int64").values, "date": et.values, "home_f": tb.team_abbreviation.map(L.FRANCHISE).values,
                          "away_f": tb.opponent_team_abbreviation.map(L.FRANCHISE).values, "venue": tb.team_home_away.values,
                          "season_type": tb.season_type.values, "total": (tb.team_score + tb.opponent_team_score).values.astype(float)})
        parts.append(h[h.venue == "home"].drop(columns="venue"))
    return pd.concat(parts, ignore_index=True)


def parse_event(ev):
    m = EVENT.match(ev)
    if not m:
        return None
    yy, mon, dd, teams = m.groups()
    return pd.Timestamp(2000 + int(yy), MONTHS[mon], int(dd)), teams[:3], teams[3:]


def kalshi_finals():
    c = sqlite3.connect(f"file:{TOT.as_posix()}?mode=ro", uri=True)
    m = pd.read_sql("select event_ticker, result, expiration_value from markets", c)
    c.close()
    m["ev"] = pd.to_numeric(m.expiration_value, errors="coerce")
    no = m[m.result == "no"]
    fin = no.groupby("event_ticker").ev.max().rename("kalshi_final").reset_index()
    allev = pd.DataFrame({"event_ticker": sorted(m.event_ticker.unique())})
    anyval = m.groupby("event_ticker").ev.apply(lambda v: bool(v.notna().any())).rename("any_value").reset_index()
    return allev.merge(fin, on="event_ticker", how="left").merge(anyval, on="event_ticker", how="left")


def compare_kalshi(hg):
    k = kalshi_finals()
    p = k.event_ticker.map(parse_event)
    k["parsed"] = p.notna()
    k = k[k.parsed].copy()
    k["date"] = [x[0] for x in p[p.notna()]]
    n_future = int((k.date >= pd.Timestamp("2026-08-01")).sum())
    k = k[k.date < pd.Timestamp("2026-08-01")].copy()                  # 2026-27 events are out of scope here
    k["away_f"] = [parse_event(e)[1] for e in k.event_ticker]
    k["home_f"] = [parse_event(e)[2] for e in k.event_ticker]
    cand = hg[hg.date >= pd.Timestamp("2025-10-01")]                 # 2025-26 (file 2026)
    rows = []
    for orient in ("away_home", "home_away"):
        kk = k if orient == "away_home" else k.rename(columns={"away_f": "home_f", "home_f": "away_f"})
        mm = CL.match_rows(kk[["date", "home_f", "away_f"]].reset_index(drop=True), cand)
        rows.append((orient, kk.reset_index(drop=True), mm))
    best = max(rows, key=lambda r: (r[2].status == "matched").sum())
    orient, kk, mm = best
    kk = kk.assign(game_id=mm.game_id.values, status=mm.status.values)
    kk = kk.merge(cand[["game_id", "total", "date", "season_type"]].rename(columns={"date": "hoopr_date"}), on="game_id", how="left")
    kk.attrs['n_future'] = n_future
    return orient, kk


def compare_odds(hg):
    conn = sqlite3.connect(f"file:{ODDS.as_posix()}?mode=ro", uri=True)
    out = {}
    for season in ("2022-23", "2023-24", "2024-25"):
        o = CL.load_odds_season(season, conn)
        o["odds_points"] = o[[c for c in ("pts_x", "pts_n") if c in o]].bfill(axis=1).iloc[:, 0]
        y = L.year_of(season)
        cand = hg[(hg.date > pd.Timestamp(f"{y - 1}-08-01")) & (hg.date < pd.Timestamp(f"{y}-08-01"))]
        mm = CL.match_rows(o[["date", "home_f", "away_f"]].reset_index(drop=True), cand)
        o = o.reset_index(drop=True).assign(game_id=mm.game_id.values, status=mm.status.values)
        o = o.merge(cand[["game_id", "total", "season_type"]], on="game_id", how="left")
        out[season] = o
    conn.close()
    return out


def main():
    hg = hoopr_games(range(2023, 2027))
    orient, kk = compare_kalshi(hg)
    L_ = ["# FINALS_CHECK - research #59 (data agreement only; no model metric)\n",
          "hoopR final total (home + away team_score) vs independent sources.  hoopR is the truth used by the study; nothing is changed.\n",
          "## 1. Kalshi end-of-game value vs hoopR, 2025-26\n",
          "Kalshi final = max expiration_value over the NO-settled strikes of the event (totals.sqlite, read-only).  "
          f"Event ticker team order that matched best: {orient} (first 3 letters = away or home as stated).\n"]
    matched = kk[kk.status == "matched"]
    comp = matched[matched.kalshi_final.notna()]
    bad = comp[comp.kalshi_final != comp.total]
    L_ += [f"- events parsed: {len(kk)}; matched to a hoopR game: {len(matched)}; unmatched: {int((kk.status == 'unmatched').sum())}; ambiguous: {int((kk.status == 'ambiguous').sum())}",
           f"- events dated 2026-08 or later (2026-27 season, out of scope): {kk.attrs.get('n_future')}",
           f"- matched events with a NO-settled strike (a final value): {len(comp)}; without: {len(matched) - len(comp)} "
           f"(of which no expiration_value recorded at all: {int((matched.kalshi_final.isna() & ~matched.any_value.fillna(False).astype(bool)).sum())})",
           f"- exact agreement: {len(comp) - len(bad)} of {len(comp)}; mismatches: {len(bad)}\n"]
    if len(bad):
        L_.append("| event | kalshi | hoopR total | hoopR game_id | date |\n|---|---|---|---|---|")
        L_ += [f"| {r.event_ticker} | {r.kalshi_final:g} | {r.total:g} | {r.game_id} | {pd.Timestamp(r.hoopr_date).date()} |" for r in bad.itertuples()]
    un = kk[kk.status != "matched"]
    if len(un):
        L_.append(f"\nUnmatched / ambiguous events ({len(un)}): " + ", ".join(un.event_ticker.head(60)))
    nofin = matched[matched.kalshi_final.isna()]
    if len(nofin):
        L_.append(f"\nMatched events with no NO-settled strike ({len(nofin)}): " + ", ".join(nofin.event_ticker.head(60)))
    L_.append("\n## 2. OddsData Points vs hoopR\n")
    L_.append("| season | OddsData rows | matched | unmatched/ambiguous | with Points | agree | mismatch |\n|---|---|---|---|---|---|---|")
    detail = []
    for season, o in compare_odds(hg).items():
        mt = o[o.status == "matched"]
        has = mt[mt.odds_points.notna()]
        bd = has[has.odds_points != has.total]
        L_.append(f"| {season} | {len(o)} | {len(mt)} | {len(o) - len(mt)} | {len(has)} | {len(has) - len(bd)} | {len(bd)} |")
        detail += [(season, r.date, r.Home, r.Away, r.game_id, r.odds_points, r.total, r.season_type) for r in bd.itertuples()]
    L_.append("\nEvery OddsData mismatch (season, date, home, away, hoopR game_id, OddsData Points, hoopR total, season_type):\n")
    L_ += [f"- {s} {pd.Timestamp(d).date()} {h} vs {a} id {g}: OddsData {p:g} vs hoopR {t:g} (type {st})" for s, d, h, a, g, p, t, st in detail] or ["- none"]
    (S.STUDY / "FINALS_CHECK.md").write_text("\n".join(L_) + "\n", encoding="utf-8")
    print("\n".join(L_[:14]))
    print(f"... OddsData mismatches listed: {len(detail)}")


if __name__ == "__main__":
    main()
