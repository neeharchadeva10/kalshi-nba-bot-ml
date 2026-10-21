"""Research log #32 data loaders (reports/output/nba_model/PREREGISTRATION_V3.md).

load_box_v3()  : hoopR-nba-data team_box / player_box 2022..2026 (seasons 2021-22 .. 2025-26) -> one structure
load_odds()    : kyleskom OddsData.sqlite (private research use only) -> {season: [rows]}
join_odds()    : odds rows <-> box games on (date, home team); returns matches + every unmatched row
Parse-only: nothing from downloaded files is executed.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BOX_DIR = ROOT / "data" / "raw" / "nba_open"
ODDS_DB = ROOT / "data" / "raw" / "nba_odds_samples" / "OddsData.sqlite"
SEASONS = [2022, 2023, 2024, 2025, 2026]          # hoopR file year = season end year
SEASON_LABEL = {2022: "2021-22", 2023: "2022-23", 2024: "2023-24", 2025: "2024-25", 2026: "2025-26"}
ESPN2CODE = {"GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS", "UTAH": "UTA", "WSH": "WAS"}
CODES = {"ATL", "BKN", "BOS", "CHA", "CHI", "CLE", "DAL", "DEN", "DET", "GSW", "HOU", "IND", "LAC", "LAL", "MEM",
         "MIA", "MIL", "MIN", "NOP", "NYK", "OKC", "ORL", "PHI", "PHX", "POR", "SAC", "SAS", "TOR", "UTA", "WAS"}
# full team name (odds file / ESPN display name) -> code. 'Charlotte Bobcats' is how 2021-22 appears in the odds file.
NAME2CODE = {
    "Atlanta Hawks": "ATL", "Brooklyn Nets": "BKN", "Boston Celtics": "BOS", "Charlotte Hornets": "CHA",
    "Charlotte Bobcats": "CHA", "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE", "Dallas Mavericks": "DAL",
    "Denver Nuggets": "DEN", "Detroit Pistons": "DET", "Golden State Warriors": "GSW", "Houston Rockets": "HOU",
    "Indiana Pacers": "IND", "LA Clippers": "LAC", "Los Angeles Clippers": "LAC", "Los Angeles Lakers": "LAL",
    "Memphis Grizzlies": "MEM", "Miami Heat": "MIA", "Milwaukee Bucks": "MIL", "Minnesota Timberwolves": "MIN",
    "New Orleans Pelicans": "NOP", "New York Knicks": "NYK", "Oklahoma City Thunder": "OKC", "Orlando Magic": "ORL",
    "Philadelphia 76ers": "PHI", "Phoenix Suns": "PHX", "Portland Trail Blazers": "POR", "Sacramento Kings": "SAC",
    "San Antonio Spurs": "SAS", "Toronto Raptors": "TOR", "Utah Jazz": "UTA", "Washington Wizards": "WAS",
}
# odds-file table per season: 2021-22 / 2022-23 use the '_new' tables (clean dates), later seasons the plain tables.
ODDS_TABLE = {2022: "odds_2021-22_new", 2023: "odds_2022-23_new", 2024: "2023-24", 2025: "2024-25"}
ET_NOTE = "game_date = ESPN local (US Eastern) date; tip = game_date_time converted to UTC"


def code_of(abbr: str) -> str:
    return ESPN2CODE.get(abbr, abbr)


def load_box_v3(box_dir: Path | None = None, seasons=None):
    """-> {'games': {gid: {...}}, 'by_key': {(date_iso, home, away): gid}, 'all_names': {name: {(season, team)}}}.
    Each game: date (iso), tip (epoch s), season (end year), stype (2 regular, 3 playoffs, 5 play-in), home, away, hs, as_,
    players {team: {norm_name: (minutes, plus_minus, points)}}. Only the 30 NBA teams (All-Star teams dropped)."""
    import pandas as pd
    import injury_parse as IP
    d = box_dir or BOX_DIR
    games = {}
    for yr in seasons or SEASONS:
        tb = pd.read_parquet(d / f"team_box_{yr}.parquet")
        for r in tb.itertuples():
            c = code_of(r.team_abbreviation)
            if c not in CODES:
                continue
            g = games.setdefault(str(r.game_id), {"gid": str(r.game_id), "date": str(r.game_date), "season": yr, "stype": int(r.season_type),
                                                  "tip": pd.Timestamp(r.game_date_time).tz_convert("UTC").timestamp(),
                                                  "home": None, "away": None, "hs": None, "as_": None,
                                                  "players": defaultdict(dict)})
            if r.team_home_away == "home":
                g["home"], g["hs"] = c, int(r.team_score)
            else:
                g["away"], g["as_"] = c, int(r.team_score)
    all_names = defaultdict(set)
    for yr in seasons or SEASONS:
        pb = pd.read_parquet(d / f"player_box_{yr}.parquet")
        pb = pb[pb["did_not_play"] == False]  # noqa: E712
        for r in pb.itertuples():
            gid = str(r.game_id)
            if gid not in games:
                continue
            c = code_of(r.team_abbreviation)
            mn = r.minutes
            if mn is None or mn != mn or mn <= 0:
                continue
            try:
                pm = int(str(r.plus_minus).replace("+", ""))
            except ValueError:
                continue
            pts = float(r.points) if r.points == r.points else 0.0
            nm = IP.norm_box_name(r.athlete_display_name)
            games[gid]["players"][c][nm] = (float(mn), pm, pts)
            all_names[nm].add((yr, c))
    games = {k: v for k, v in games.items() if v["home"] and v["away"] and v["hs"] is not None and v["as_"] is not None
             and v["hs"] != v["as_"]}
    by_key = {}
    dup = []
    for gid, g in games.items():
        k = (g["date"], g["home"], g["away"])
        if k in by_key:
            dup.append(k)
        by_key[k] = gid
    return {"games": games, "by_key": by_key, "all_names": all_names, "duplicate_keys": dup}


def to_int_odds(x):
    """'+145\\xa0' / '-125' / 105 -> int, else None."""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return int(x) if x == x else None
    s = str(x).replace("\xa0", "").replace("+", "").strip()
    try:
        return int(float(s))
    except ValueError:
        return None


def load_odds(db: Path | None = None):
    """-> {season: [dict(date, home, away, ml_h, ml_a, spread, ou, raw_home, raw_away)]} for the four test/training seasons."""
    con = sqlite3.connect(f"file:{(db or ODDS_DB).as_posix()}?mode=ro", uri=True)
    out = {}
    for yr, tab in ODDS_TABLE.items():
        rows = []
        for dt, h, a, ou, sp, mh, ma in con.execute(f'select Date, Home, Away, OU, Spread, ML_Home, ML_Away from "{tab}"'):
            rows.append({"date": str(dt)[:10], "home_name": h, "away_name": a, "home": NAME2CODE.get(h), "away": NAME2CODE.get(a),
                         "ml_h": to_int_odds(mh), "ml_a": to_int_odds(ma), "spread": sp, "ou": ou})
        out[yr] = rows
    con.close()
    return out


def join_odds(box, odds_rows, season):
    """Join one season's odds rows to box games on (date, home); exact date first, then +-1 day (shifted/postponed dates).
    Returns (matches {gid: odds_row + 'day_offset'}, report). Away team must agree, else the row is 'away_mismatch' (not joined)."""
    by_dh = defaultdict(list)
    for gid, g in box["games"].items():
        if g["season"] == season:
            by_dh[(g["date"], g["home"])].append(gid)
    matches, rep = {}, defaultdict(list)
    used_box = {}
    order = sorted(range(len(odds_rows)), key=lambda i: (odds_rows[i]["date"], i))
    # pass 1: exact date; pass 2: +-1 day for the rest
    pending = []
    for i in order:
        o = odds_rows[i]
        if o["home"] is None or o["away"] is None:
            rep["unknown_team_name"].append((o["date"], o["home_name"], o["away_name"]))
            continue
        if o["ml_h"] is None or o["ml_a"] is None or o["ml_h"] == 0 or o["ml_a"] == 0:
            rep["missing_or_bad_moneyline"].append((o["date"], o["home"], o["away"], o["ml_h"], o["ml_a"]))
            continue
        pending.append(i)
    rest = []
    for i in pending:
        o = odds_rows[i]
        cands = by_dh.get((o["date"], o["home"]), [])
        gid = cands[0] if len(cands) == 1 else None
        if gid and box["games"][gid]["away"] == o["away"] and gid not in used_box:
            used_box[gid] = i
            matches[gid] = dict(o, day_offset=0)
        elif gid and box["games"][gid]["away"] != o["away"]:
            rep["away_mismatch"].append((o["date"], o["home"], o["away"], box["games"][gid]["away"]))
        elif len(cands) > 1:
            rep["ambiguous_same_day"].append((o["date"], o["home"]))
        else:
            rest.append(i)
    for i in rest:
        o = odds_rows[i]
        d0 = date.fromisoformat(o["date"])
        got = None
        for dd in (-1, 1):
            k = ((d0 + timedelta(days=dd)).isoformat(), o["home"])
            for gid in by_dh.get(k, []):
                if box["games"][gid]["away"] == o["away"] and gid not in used_box:
                    got = (gid, dd)
                    break
            if got:
                break
        if got:
            used_box[got[0]] = i
            matches[got[0]] = dict(o, day_offset=got[1])
            rep["matched_off_by_one_day"].append((o["date"], o["home"], o["away"], got[1]))
        else:
            rep["odds_row_unmatched"].append((o["date"], o["home"], o["away"]))
    n_box = sum(1 for g in box["games"].values() if g["season"] == season)
    rep["box_games_without_odds"] = [(g["date"], g["home"], g["away"], g["stype"]) for gid, g in box["games"].items()
                                     if g["season"] == season and gid not in matches]
    summary = {"odds_rows": len(odds_rows), "box_games": n_box, "matched": len(matches)}
    summary.update({k: len(v) for k, v in rep.items()})
    return matches, dict(rep), summary


# ------------------------------------------------------------------ odds math
def implied_prob(american: int) -> float:
    """100/(p+100) if p > 0 else -p/(-p+100)."""
    p = float(american)
    return 100.0 / (p + 100.0) if p > 0 else -p / (-p + 100.0)


def profit_per_dollar(american: int, won: bool) -> float:
    """$1 stake: win -> p/100 (p>0) or 100/-p (p<0); lose -> -1."""
    if not won:
        return -1.0
    p = float(american)
    return p / 100.0 if p > 0 else 100.0 / (-p)


def devig_home(ml_h: int, ml_a: int) -> float:
    """de-vigged home win probability = home implied / (home implied + away implied)."""
    ih, ia = implied_prob(ml_h), implied_prob(ml_a)
    return ih / (ih + ia)
